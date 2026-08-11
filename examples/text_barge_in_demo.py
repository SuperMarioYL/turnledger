"""Text-streaming barge-in contrast demo.

Run to see TurnLedger resume from the interrupted token vs the default
restart-from-top behavior. This is the front-of-README demo artifact
(mvp_plan assets.demo.target_path).

    python examples/text_barge_in_demo.py

Or drive it programmatically to capture the contrast without a keyboard:

    from turnledger.adapters.text_stream import TextBargeInSimulator
    sim = TextBargeInSimulator(TOKENS, interrupt_at=14, user_utterance="add multi-region")
    result = sim.contrast()
    print("ledger :", "".join(result["ledger_mode"]["emitted"]))
    print("restart:", "".join(result["restart_mode"]["emitted"]))
"""

from __future__ import annotations

import sys
from typing import List

# Allow running this file directly (python examples/...) without installing.
if __package__ in (None, ""):
    sys.path.insert(0, ".")
    from turnledger.adapters.text_stream import TextBargeInSimulator  # type: ignore
else:
    from turnledger.adapters.text_stream import TextBargeInSimulator

# A canned agent utterance long enough that the contrast is legible. Stored
# as a token list (word + trailing space) so the simulator can interrupt at a
# clean word boundary.
_UTTERANCE_TEXT = (
    "So the Pro tier covers five seats and the Team plan adds multi-region "
    "storage plus audit-log export for production voice-bot fleets over fifty seats."
)


def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    words = text.split(" ")
    for idx, word in enumerate(words):
        tokens.append(word)
        if idx < len(words) - 1:
            tokens.append(" ")
    return tokens


UTTERANCE: List[str] = _tokenize(_UTTERANCE_TEXT)


def main() -> None:
    # Barge in at the "storage" token — the ledger-mode run continues from
    # there; the restart-mode run re-emits the whole prefix.
    interrupt_at = UTTERANCE.index("storage")
    sim = TextBargeInSimulator(
        UTTERANCE,
        interrupt_at=interrupt_at,
        user_utterance="— and the audit-log export?",
        turn_id="turn-example",
    )
    result = sim.contrast()
    ledger_out = "".join(result["ledger_mode"]["emitted"])
    restart_out = "".join(result["restart_mode"]["emitted"])

    bold = "\033[1m"
    green = "\033[32m"
    amber = "\033[33m"
    reset = "\033[0m"
    print(f"\n{bold}TurnLedger barge-in contrast{reset}")
    print("=" * 70)
    print(f"{green}resume-continue :{reset} {ledger_out}")
    print(f"{amber}drop-and-restart:{reset} {restart_out}")
    print("=" * 70)
    print(
        "ledger mode resumes from the interrupted token; restart mode "
        "re-emits the prefix — that visible double-emission is the gap "
        "TurnLedger closes."
    )
    print(
        f"ledger history (audit): {len(result['ledger_mode']['history'])} entries"
    )


if __name__ == "__main__":
    main()
