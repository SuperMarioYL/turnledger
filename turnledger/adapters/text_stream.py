"""Text-streaming adapter — the m1 analog of the m2 voice barge-in.

A real-time voice agent has no lightweight "interrupt" surface you can test
in 10 seconds — you need a LiveKit room, an STT, a TTS, a model. So m1 ships
a text-streaming analog: a canned agent utterance is streamed token by token
to the terminal, and the user barges in by pressing Enter. The same
save/yield/resume primitive that the m2 LiveKit adapter wires to
``OverlappingSpeechEvent`` is wired here to a keyboard predicate.

This module contains two things:

* ``TextStreamAgent`` — the pure save/yield/resume loop over a token list.
  No I/O. An injected ``should_interrupt`` predicate and ``on_yield``
  callback own the input surface, so the agent is testable headless.
* ``TextBargeInSimulator`` — drives the agent through a scripted barge-in
  and produces the visible contrast: ledger mode resumes from the
  interrupted token; restart mode (the default voice-assistant behavior)
  aborts and restarts from the top.
"""

from __future__ import annotations

from typing import Any, Callable

from ..cursor import TurnCursor
from ..ledger import LedgerEntry
from ..protocol import TurnLedger

# A predicate: (token_index, token) -> "should the user interrupt right now?"
InterruptPredicate = Callable[[int, str], bool]
# A callback: (suspended_entry) -> user_utterance. The callback owns the input
# surface (print a prompt, read a line) and is responsible for consuming /
# clearing the interrupt source so the same barge-in is not processed twice.
YieldCallback = Callable[[LedgerEntry], str]


class TextStreamAgent:
    """A minimal streaming-text agent that uses the ledger for save/yield/resume.

    The agent streams tokens from a canned utterance. Before emitting each
    token it asks ``should_interrupt`` whether the user has barged in; if so,
    it ``suspend``s via the ledger, hands the suspended entry to ``on_yield``
    (which returns the user's utterance), then ``resume``s continuing from
    the interrupted token — instead of restarting from the top.

    This is the same handshake the m2 LiveKit adapter performs for voice;
    only the interrupt surface differs (keyboard vs. ``OverlappingSpeechEvent``).
    """

    def __init__(
        self,
        tokens: list[str],
        ledger: TurnLedger,
        *,
        turn_id: str = "turn-1",
        step_index: int = 0,
    ) -> None:
        self.tokens = list(tokens)
        self.ledger = ledger
        self.turn_id = turn_id
        self.step_index = step_index
        self._emitted: list[str] = []

    @property
    def emitted(self) -> list[str]:
        """Tokens the agent has actually emitted this run (agent voice only)."""
        return list(self._emitted)

    def run(
        self,
        *,
        should_interrupt: InterruptPredicate | None = None,
        on_yield: YieldCallback | None = None,
        start_token: int = 0,
        restart_on_interrupt: bool = False,
    ) -> list[str]:
        """Stream the turn, honoring save/yield/resume.

        Args:
            should_interrupt: ``(token_index, token) -> bool``. Called before
                emitting each token; ``True`` means a barge-in was detected.
                Defaults to "never".
            on_yield: ``suspended_entry -> user_utterance``. Called when the
                agent yields the floor. The callback owns the input surface
                and must consume/clear the interrupt source. Defaults to a
                no-op returning an empty string.
            start_token: where to (re)start streaming — used by ``resume()``.
            restart_on_interrupt: contrast mode. If ``True``, on barge-in the
                agent ABORTS and restarts from token 0 (the default
                voice-assistant drop-and-restart behavior), instead of
                resuming. The ledger still records the suspend for audit.

        Returns:
            The list of tokens the agent emitted (agent voice only; the
            user's utterance is handled by ``on_yield`` and not included).
        """
        if should_interrupt is None:
            should_interrupt = lambda _i, _t: False
        if on_yield is None:
            on_yield = lambda _entry: ""

        i = start_token
        while i < len(self.tokens):
            token = self.tokens[i]
            if should_interrupt(i, token):
                entry = self.ledger.suspend(
                    self.turn_id,
                    cursor=TurnCursor(
                        token_index=i,
                        step_index=self.step_index,
                        sub_step="llm:streaming",
                    ),
                    payload={
                        "emitted_so_far": list(self._emitted),
                        "adapter": "text_stream",
                    },
                )
                # Hand the floor to the user; the callback blocks until the
                # user finishes and returns their utterance.
                user_utterance = on_yield(entry)
                if restart_on_interrupt:
                    # Default voice-assistant behavior: drop the in-flight
                    # continuation state and re-speak the turn from the top.
                    # The visible output keeps what the user already heard
                    # (the prefix) so the contrast — prefix spoken twice —
                    # is legible; only the continuation cursor resets.
                    i = 0
                    continue
                # TurnLedger resume-continue: pick up from the interrupted token.
                resumed = self.ledger.resume(self.turn_id)
                if resumed is None:
                    break
                i = resumed.cursor.token_index
                continue

            self.ledger.report(
                self.turn_id,
                cursor=TurnCursor(
                    token_index=i,
                    step_index=self.step_index,
                    sub_step="llm:streaming",
                ),
                payload={
                    "emitted_so_far": list(self._emitted),
                    "adapter": "text_stream",
                    "user_utterance_pending": False,
                },
            )
            self._emitted.append(token)
            i += 1
        return list(self._emitted)

    def resume_from_ledger(self) -> list[str]:
        """Continue streaming from the ledger's last suspended cursor.

        Convenience wrapper: resumes the turn via the ledger and streams the
        remaining tokens, honoring further barge-ins through the same
        ``run`` loop. Use when the caller has already handled the user
        utterance out-of-band and just needs the agent to pick up.
        """
        resumed = self.ledger.resume(self.turn_id)
        if resumed is None:
            return []
        return self.run(start_token=resumed.cursor.token_index)


class TextBargeInSimulator:
    """Drives ``TextStreamAgent`` through a scripted barge-in (deterministic).

    For reproducible demos and tests: barge in at ``interrupt_at`` with a fixed
    ``user_utterance``, run once in ledger mode and once in restart mode, and
    compare the emitted tokens. The ledger-mode run resumes from the
    interrupted token; the restart-mode run re-emits the prefix — that
    visible contrast is the whole pitch.
    """

    def __init__(
        self,
        tokens: list[str],
        *,
        interrupt_at: int,
        user_utterance: str,
        turn_id: str = "turn-1",
    ) -> None:
        self.tokens = list(tokens)
        self.interrupt_at = interrupt_at
        self.user_utterance = user_utterance
        self.turn_id = turn_id

    def _predicate(self) -> InterruptPredicate:
        fired = {"done": False}

        def _check(i: int, _token: str) -> bool:
            if fired["done"]:
                return False
            if i == self.interrupt_at:
                fired["done"] = True
                return True
            return False

        return _check

    def _on_yield(self, captured: list[LedgerEntry], user_seen: list[str]) -> YieldCallback:
        def _cb(entry: LedgerEntry) -> str:
            captured.append(entry)
            user_seen.append(self.user_utterance)
            return self.user_utterance

        return _cb

    def run_ledger_mode(self, ledger: TurnLedger | None = None) -> dict[str, Any]:
        """Resume-from-interrupted-token mode (TurnLedger on)."""
        if ledger is None:
            from .. import InMemoryLedger

            ledger = InMemoryLedger()
        agent = TextStreamAgent(self.tokens, ledger, turn_id=self.turn_id)
        captured: list[LedgerEntry] = []
        user_seen: list[str] = []
        emitted = agent.run(
            should_interrupt=self._predicate(),
            on_yield=self._on_yield(captured, user_seen),
        )
        return {
            "mode": "ledger",
            "emitted": emitted,
            "user_utterances": user_seen,
            "suspended_entries": captured,
            "history": ledger.history(self.turn_id),
            "ledger": ledger,
        }

    def run_restart_mode(self, ledger: TurnLedger | None = None) -> dict[str, Any]:
        """Drop-and-restart mode (default voice-assistant behavior)."""
        if ledger is None:
            from .. import InMemoryLedger

            ledger = InMemoryLedger()
        agent = TextStreamAgent(self.tokens, ledger, turn_id=self.turn_id)
        captured: list[LedgerEntry] = []
        user_seen: list[str] = []
        emitted = agent.run(
            should_interrupt=self._predicate(),
            on_yield=self._on_yield(captured, user_seen),
            restart_on_interrupt=True,
        )
        return {
            "mode": "restart",
            "emitted": emitted,
            "user_utterances": user_seen,
            "suspended_entries": captured,
            "history": ledger.history(self.turn_id),
            "ledger": ledger,
        }

    def contrast(self) -> dict[str, Any]:
        """Run both modes and return the side-by-side comparison."""
        return {
            "ledger_mode": self.run_ledger_mode(),
            "restart_mode": self.run_restart_mode(),
        }


__all__ = ["TextStreamAgent", "TextBargeInSimulator", "InterruptPredicate", "YieldCallback"]
