"""TurnCursor — where in an in-flight turn we were when interrupted.

A cursor is a position inside a single streaming agent turn. It is the
load-bearing piece of the save/yield/resume primitive: it is what lets the
same agent resume *continuing* its in-flight turn rather than restart it.

The cursor is intentionally framework-agnostic. A text-stream adapter fills it
with a token index; a LiveKit voice adapter fills it with a chunk / step /
tool-call-args position. The ledger never interprets the fields — it only
stores and replays them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TurnCursor(BaseModel):
    """A position inside one in-flight agent turn.

    Attributes:
        token_index: Position in the output token stream (0-based). For
            text adapters this is the token offset; for voice adapters it is
            the TTS chunk / segment offset.
        step_index: Which agent-flow step was in progress (0-based). A step
            is one LLM call or one tool-call boundary.
        sub_step: Sub-step id within the step, e.g. ``"tool_call:args"``,
            ``"llm:streaming"``, ``"tts:playout"``. Free-form string the
            adapter owns.
    """

    token_index: int = Field(default=0, ge=0)
    step_index: int = Field(default=0, ge=0)
    sub_step: str = ""

    def advance_token(self) -> "TurnCursor":
        """Return a new cursor advanced by one token (immutable update)."""
        return self.model_copy(update={"token_index": self.token_index + 1})

    def with_sub_step(self, sub_step: str) -> "TurnCursor":
        return self.model_copy(update={"sub_step": sub_step})

    def with_step(self, step_index: int, sub_step: str = "") -> "TurnCursor":
        return self.model_copy(update={"step_index": step_index, "sub_step": sub_step})
