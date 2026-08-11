"""``turnledger`` CLI — the text-streaming barge-in contrast demo.

Run::

    turnledger demo

A canned agent utterance streams token by token to the terminal. Press Enter
mid-stream to barge in; type a line and Enter to finish. The agent yields the
floor, then **resumes continuing from the interrupted token** — toggle
contrast mode to see the default drop-and-restart behavior side by side.

This is the m1 happy path (mvp_plan §3): ``pip install turnledger`` then
``turnledger demo`` to the 30-second "wow" in under 60 seconds.
"""

from __future__ import annotations

import queue
import shutil
import sys
import threading
import time
from typing import Iterator

import typer

from . import InMemoryLedger, SQLiteLedger, TurnCursor
from .adapters.text_stream import TextStreamAgent
from .ledger import LedgerEntry

app = typer.Typer(
    add_completion=False,
    help="TurnLedger — an interruption-continuation ledger for real-time agents.",
)

# A canned agent utterance long enough that a human can barge in mid-stream.
DEFAULT_UTTERANCE = (
    "So if you want to pick a plan, the Pro tier covers five concurrent agent "
    "seats with the durable ledger and the observability API, and the Team plan "
    "adds multi-region storage plus audit-log export, which is what most "
    "production voice-bot fleets end up needing once they pass fifty seats."
)


def _tokenize(text: str) -> list[str]:
    """Split into tokens keeping whitespace attached (whitespace-split + space)."""
    parts = text.split(" ")
    tokens: list[str] = []
    for idx, part in enumerate(parts):
        tokens.append(part)
        if idx < len(parts) - 1:
            tokens.append(" ")
    return tokens


class _KeyboardBargeIn:
    """A background stdin reader that turns Enter into a barge-in event.

    Each line the user types after the first empty Enter is treated as the
    user utterance. The predicate the agent polls is "has a barge-in arrived
    since the last one was consumed"; ``on_yield`` drains the utterance
    queue (set by the listener before the flag, so it is always available).
    """

    def __init__(self) -> None:
        self._flag = threading.Event()
        self._q: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _read_loop(self) -> None:
        # Read stdin line by line. An empty line (just Enter) signals a
        # barge-in; the *next* line is the user utterance. If the user
        # types a non-empty line as the barge-in itself (faster UX), treat
        # that line as both the barge-in signal and the utterance.
        while not self._stop.is_set():
            try:
                line = sys.stdin.readline()
            except (OSError, ValueError):
                break
            if line == "":
                break  # EOF
            line = line.rstrip("\n")
            if line:
                # Non-empty line: barge-in + utterance in one.
                self._q.put(line)
                self._flag.set()
            else:
                # Empty Enter: barge-in first; await the utterance line.
                self._flag.set()
                try:
                    utter = sys.stdin.readline()
                except (OSError, ValueError):
                    break
                if utter == "":
                    self._q.put("")
                    break
                self._q.put(utter.rstrip("\n"))

    def predicate(self) -> "callable":
        def _check(_i: int, _t: str) -> bool:
            return self._flag.is_set()

        return _check

    def on_yield(self, entry: LedgerEntry) -> str:
        """Drain the user utterance (the queue was filled before the flag)."""
        utterance = self._q.get()
        self._flag.clear()
        return utterance


def _print_slow(tokens: Iterator[tuple[int, str]], delay: float) -> None:
    for _i, tok in tokens:
        sys.stdout.write(tok)
        sys.stdout.flush()
        time.sleep(delay)


def _print_user_block(utterance: str) -> None:
    dim = "\033[2m"
    cyan = "\033[36m"
    reset = "\033[0m"
    sys.stdout.write(f"\n{dim}  ⤵ user barge-in:{reset} {cyan}{utterance}{reset}\n")
    sys.stdout.flush()


def _print_yield_header() -> None:
    dim = "\033[2m"
    reset = "\033[0m"
    sys.stdout.write(f"{dim}  ⏸ agent yielded the floor{reset}\n")
    sys.stdout.flush()


def _print_resume_header(token_index: int) -> None:
    dim = "\033[2m"
    green = "\033[32m"
    reset = "\033[0m"
    sys.stdout.write(f"{green}  ▶ resume from token #{token_index}{reset}\n{dim}  ⋯ continuing:{reset} ")
    sys.stdout.flush()


def _print_restart_header() -> None:
    dim = "\033[2m"
    amber = "\033[33m"
    reset = "\033[0m"
    sys.stdout.write(f"\n{amber}  ↻ restart (drop-and-restart default){reset}\n{dim}  ⋯ from the top:{reset} ")
    sys.stdout.flush()


def _print_agent_header(mode: str) -> None:
    bold = "\033[1m"
    reset = "\033[0m"
    label = "TurnLedger (resume-continue)" if mode == "ledger" else "Default (drop-and-restart)"
    sys.stdout.write(f"\n{bold}▶ {label}{reset}\n  ")
    sys.stdout.flush()


def _print_ledger_audit(entry: LedgerEntry | None, ledger: InMemoryLedger, turn_id: str) -> None:
    dim = "\033[2m"
    reset = "\033[0m"
    sys.stdout.write(f"\n{dim}  [ledger] turn={turn_id} suspended_entries={len(ledger.history(turn_id))}{reset}\n")
    if entry is not None:
        sys.stdout.write(
            f"{dim}  [ledger] cursor=token#{entry.cursor.token_index} step={entry.step_index}"
            f" sub={entry.cursor.sub_step} payload_keys={sorted(entry.payload)}{reset}\n"
        )
    sys.stdout.flush()


def _run_mode(
    tokens: list[str],
    mode: str,
    *,
    token_delay: float,
    ledger: InMemoryLedger,
    turn_id: str,
) -> None:
    kb = _KeyboardBargeIn()
    _print_agent_header(mode)
    agent = TextStreamAgent(tokens, ledger, turn_id=turn_id)

    suspended_entry: LedgerEntry | None = None

    def _on_yield(entry: LedgerEntry) -> str:
        nonlocal suspended_entry
        suspended_entry = entry
        _print_yield_header()
        utter = kb.on_yield(entry)
        _print_user_block(utter)
        if mode == "restart":
            _print_restart_header()
        else:
            assert entry.cursor is not None
            _print_resume_header(entry.cursor.token_index)
        return utter

    kb.start()
    try:
        # Drive the agent token by token so we can pace the playout and
        # surface the yield/resume markers in line.
        should_interrupt = kb.predicate()
        emitted: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if should_interrupt(i, tok):
                entry = ledger.suspend(
                    turn_id,
                    cursor=TurnCursor(token_index=i, step_index=0, sub_step="llm:streaming"),
                    payload={"emitted_so_far": list(emitted), "adapter": "text_stream"},
                )
                suspended_entry = entry
                _print_yield_header()
                utter = kb.on_yield(entry)
                _print_user_block(utter)
                if mode == "restart":
                    _print_restart_header()
                    emitted = []
                    i = 0
                    continue
                resumed = ledger.resume(turn_id)
                _print_resume_header(resumed.cursor.token_index if resumed else i)
                i = resumed.cursor.token_index if resumed else i
                continue
            ledger.report(
                turn_id,
                cursor=TurnCursor(token_index=i, step_index=0, sub_step="llm:streaming"),
                payload={"emitted_so_far": list(emitted)},
            )
            sys.stdout.write(tok)
            sys.stdout.flush()
            emitted.append(tok)
            time.sleep(token_delay)
            i += 1
    finally:
        kb.stop()
    sys.stdout.write("\n")
    _print_ledger_audit(suspended_entry, ledger, turn_id)


@app.command()
def demo(
    contrast: bool = typer.Option(
        True,
        "--contrast/--no-contrast",
        help="Run both resume-continue and drop-and-restart modes side by side.",
    ),
    token_delay: float = typer.Option(
        0.08, "--token-delay", "-d", help="Seconds between streamed tokens (lower = faster)."
    ),
    utterance: str = typer.Option(
        DEFAULT_UTTERANCE, "--utterance", "-u", help="The canned agent utterance to stream."
    ),
    store: str = typer.Option(
        "memory", "--store", help="memory | sqlite (SQLite audit log at ./turnledger.db)."
    ),
    turn_id: str = typer.Option("turn-demo", "--turn-id", help="Ledger turn id."),
) -> None:
    """Stream an agent turn; press Enter mid-stream to barge in.

    The agent yields the floor, then resumes continuing from the interrupted
    token (TurnLedger mode) — vs the default drop-and-restart behavior.
    """
    tokens = _tokenize(utterance)
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"
    cols = shutil.get_terminal_size((80, 24)).columns
    title = "TurnLedger — barge-in contrast demo"
    sys.stdout.write(f"\n{bold}{title}{reset}\n")
    sys.stdout.write(f"{dim}Press Enter mid-stream to barge in; type a line + Enter to finish.{reset}\n")
    sys.stdout.write("=" * min(cols, 80) + "\n")

    def _make_ledger() -> InMemoryLedger:
        if store == "sqlite":
            return SQLiteLedger("turnledger.db")  # type: ignore[return-value]
        return InMemoryLedger()

    if contrast:
        _run_mode(tokens, "ledger", token_delay=token_delay, ledger=_make_ledger(), turn_id=turn_id)
        sys.stdout.write("\n" + "-" * min(cols, 80) + "\n")
        _run_mode(tokens, "restart", token_delay=token_delay, ledger=_make_ledger(), turn_id=turn_id)
    else:
        _run_mode(tokens, "ledger", token_delay=token_delay, ledger=_make_ledger(), turn_id=turn_id)
    sys.stdout.write("\n")


@app.command()
def info() -> None:
    """Print the version and the core API surface."""
    from . import __version__

    typer.echo(f"turnledger {__version__}")
    typer.echo("core: TurnCursor / LedgerEntry / Ledger / InMemoryLedger / SQLiteLedger")
    typer.echo("adapters: turnledger.adapters.text_stream (m1) / .livekit (m2, voice)")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
