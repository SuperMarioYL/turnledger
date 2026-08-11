"""LiveKit agents adapter — binds the ledger to LiveKit's real interrupt surface.

This is the m2 ship target (mvp_plan §5). It wires the framework-agnostic
save/yield/resume primitive to LiveKit's *actual* barge-in API — no invented
hooks. Every name below is the real livekit-agents surface, verified against
the ``livekit/agents`` main branch:

  * ``livekit.agents.voice.Agent`` — configured with
    ``allow_interruptions=True`` (the deprecated kwarg, still functional) and
    ``interruption_detection`` (a read property, ``Literal["adaptive","vad"]``)
    set via ``turn_handling=TurnHandlingOptions(interruption=...)``.
  * ``livekit.agents.voice.AgentSession`` — an ``rtc.EventEmitter``; the
    ``"overlapping_speech"`` event carries an ``OverlappingSpeechEvent``
    (``is_interruption`` flag) — that is the barge-in signal.
  * ``livekit.agents.voice.SpeechHandle.allow_interruptions`` — per-speech
    override (property + setter).
  * ``livekit.agents.voice.RunContext.disallow_interruptions()`` —
    per-function-call override (delegates to the SpeechHandle setter).
  * ``Agent.on_user_turn_completed(turn_ctx, new_message)`` — the resume
    hook: the user has finished, the agent is about to respond, so the ledger
    resumes continuing from the interrupted point.

Wiring (mvp_plan §4):
  * ``OverlappingSpeechEvent`` (``is_interruption=True``)  ->  ``ledger.suspend(turn_id)``
  * ``Agent.on_user_turn_completed``                        ->  ``ledger.resume(turn_id)``
  * ``Agent`` configured with ``allow_interruptions=True`` + ``interruption_detection``
  * per-speech override:  ``SpeechHandle.allow_interruptions = False``
  * per-function-call override:  ``RunContext.disallow_interruptions()``

The ledger stays framework-agnostic; this adapter owns the mapping.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable

from ..cursor import TurnCursor
from ..ledger import LedgerEntry
from ..protocol import TurnLedger

# LiveKit is a heavy optional dependency — only needed for the m2 voice demo.
# Importing ``turnledger`` (the core) must not require it. We try the import
# here and degrade to a clear stub if the package is absent.
try:
    from livekit.agents.voice import (
        Agent as _LKAgent,
        AgentSession as _LKAgentSession,
        RunContext as _LKRunContext,
        SpeechHandle as _LKSpeechHandle,
    )
    from livekit.agents.voice.turn import (
        InterruptionOptions as _LKInterruptionOptions,
        TurnHandlingOptions as _LKTurnHandlingOptions,
    )
    from livekit.agents.voice.events import (
        OverlappingSpeechEvent as _LKOverlappingSpeechEvent,
        SpeechCreatedEvent as _LKSpeechCreatedEvent,
    )
    from livekit import llm as _lk_llm

    _LIVEKIT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when livekit is missing
    _LIVEKIT_AVAILABLE = False
    _LKAgent = None  # type: ignore[assignment]
    _LKAgentSession = None  # type: ignore[assignment]
    _LKRunContext = None  # type: ignore[assignment]
    _LKSpeechHandle = None  # type: ignore[assignment]
    _LKInterruptionOptions = None  # type: ignore[assignment]
    _LKTurnHandlingOptions = None  # type: ignore[assignment]
    _LKOverlappingSpeechEvent = None  # type: ignore[assignment]
    _LKSpeechCreatedEvent = None  # type: ignore[assignment]
    _lk_llm = None  # type: ignore[assignment]


_LIVEKIT_INSTALL_HINT = (
    "livekit-agents is required for the TurnLedger LiveKit adapter. "
    "Install it with: pip install 'livekit-agents>=0.7'"
)


def _require_livekit() -> None:
    if not _LIVEKIT_AVAILABLE:
        raise ImportError(_LIVEKIT_INSTALL_HINT)


if _LIVEKIT_AVAILABLE:

    class LiveKitLedgerAgent(_LKAgent):  # type: ignore[misc, valid-type]
        """A LiveKit ``Agent`` that suspends/resumes its turn via TurnLedger.

        Configured out of the box with ``allow_interruptions=True`` and
        ``interruption_detection="adaptive"`` (the ML-based detector). On an
        ``OverlappingSpeechEvent`` with ``is_interruption=True`` it calls
        ``ledger.suspend()``; on ``on_user_turn_completed`` it calls
        ``ledger.resume()`` so the agent continues from the interrupted point
        instead of restarting the turn.

        Per-speech override (the LiveKit native surface, unchanged):
        ``speech_handle.allow_interruptions = False``.
        Per-function-call override:
        ``run_ctx.disallow_interruptions()`` (delegates to the SpeechHandle).

        Example:
            >>> from turnledger import InMemoryLedger
            >>> from turnledger.adapters.livekit import LiveKitLedgerAgent
            >>> class MyAgent(LiveKitLedgerAgent):
            ...     def __init__(self):
            ...         super().__init__(
            ...             instructions="You are a helpful voice agent.",
            ...             ledger=InMemoryLedger(),
            ...             interruption_detection="adaptive",
            ...         )
            >>> # then wire MyAgent into an AgentSession as usual; the agent
            >>> # subscribes to "overlapping_speech" on the session in on_enter().
        """

        def __init__(
            self,
            *,
            instructions: str,
            ledger: TurnLedger,
            interruption_detection: str = "adaptive",
            turn_id: str | None = None,
            **kwargs: Any,
        ) -> None:
            # Build the turn_handling options that set BOTH allow_interruptions
            # (enabled=True) and interruption_detection (mode=...). This is the
            # real, non-deprecated way to express ``Agent(allow_interruptions=True,
            # interruption_detection=...)`` — allow_interruptions is a deprecated
            # kwarg migrated into turn_handling["interruption"]["enabled"], and
            # interruption_detection is a read-only property sourced from
            # turn_handling["interruption"]["mode"].
            turn_handling = kwargs.pop("turn_handling", None) or _LKTurnHandlingOptions(
                interruption=_LKInterruptionOptions(
                    enabled=True,
                    mode=interruption_detection,  # "adaptive" | "vad"
                )
            )
            super().__init__(
                instructions=instructions,
                turn_handling=turn_handling,
                **kwargs,
            )
            self._ledger = ledger
            self._turn_id = turn_id or "turn-lk-default"
            self._speech_counter = 0
            self._current_speech: _LKSpeechHandle | None = None
            self._resumed_entry: LedgerEntry | None = None

        # ---------------------------------------------------- configuration

        @property
        def ledger(self) -> TurnLedger:
            return self._ledger

        @property
        def turn_id(self) -> str:
            return self._turn_id

        @turn_id.setter
        def turn_id(self, value: str) -> None:
            self._turn_id = value

        @property
        def resumed_entry(self) -> LedgerEntry | None:
            """The most recent resume entry; the agent loop reads this to
            know where to continue from."""
            return self._resumed_entry

        # -------------------------------------------------------- LiveKit hooks

        async def on_enter(self) -> None:
            """Subscribe to the session's overlapping-speech events.

            ``self.session`` is the live ``AgentSession`` once the agent has
            entered it. The ``"overlapping_speech"`` event is the barge-in
            signal LiveKit emits when the user talks over the agent.
            """
            await super().on_enter()
            self.session.on("overlapping_speech", self._on_overlapping_speech)
            self.session.on("speech_created", self._on_speech_created)

        def _on_speech_created(self, ev: _LKSpeechCreatedEvent) -> None:
            """Track the active speech handle so suspend() can snapshot it."""
            self._speech_counter += 1
            self._current_speech = getattr(ev, "speech_handle", None)

        def _on_overlapping_speech(self, ev: _LKOverlappingSpeechEvent) -> None:
            """Barge-in detected -> suspend the in-flight turn via the ledger.

            LiveKit's own behavior on overlap is to *cancel* the speech (the
            drop-and-restart gap). We intercept here and *also* save the
            suspended mid-step state in the ledger, so ``resume()`` can
            restore it instead of restarting.
            """
            # Only suspend on a genuine interruption, not on a backchannel
            # overlap that LiveKit classified as non-interrupting.
            if not getattr(ev, "is_interruption", False):
                return

            speech = self._current_speech
            cursor = TurnCursor(
                token_index=self._speech_counter,
                step_index=getattr(speech, "_num_steps", 1) if speech else 1,
                sub_step="voice:playout",
            )
            payload: dict[str, Any] = {
                "adapter": "livekit",
                "speech_id": getattr(speech, "_id", None) if speech else None,
                "agent_ended": getattr(ev, "agent_ended", False),
                "detected_at": getattr(ev, "detected_at", None),
            }
            self._ledger.suspend(self._turn_id, cursor=cursor, payload=payload)

        async def on_user_turn_completed(
            self, turn_ctx: "_lk_llm.ChatContext", new_message: "_lk_llm.ChatMessage"
        ) -> None:
            """User finished speaking -> resume continuing from the interrupted point.

            LiveKit calls this right before the agent responds to the user's
            turn. The default behavior restarts the agent turn from scratch.
            We override to first resume the ledger: if a suspend entry
            exists, ``resume()`` returns its cursor+payload so the agent's
            next generation continues from where it was interrupted.
            """
            # Let the base hook run first (no-op by default; subclasses may
            # edit the chat context).
            await super().on_user_turn_completed(turn_ctx, new_message)
            self._resumed_entry = self._ledger.resume(self._turn_id)

        # --------------------------------------------------- override helpers

        def disallow_interruptions_on_current_speech(self) -> None:
            """Per-speech override: freeze the current speech against barge-in.

            Thin wrapper over the real ``SpeechHandle.allow_interruptions``
            setter so callers don't have to reach into the framework. Use
            when an agent step must not be interrupted (e.g. reading a legal
            disclaimer). Raise ``RuntimeError`` if the speech is already
            interrupted (LiveKit's own contract).
            """
            if self._current_speech is None:
                return
            self._current_speech.allow_interruptions = False  # type: ignore[attr-defined]

        @staticmethod
        def disallow_interruptions_in_tool(run_ctx: _LKRunContext) -> None:
            """Per-function-call override: freeze the speech for this tool call.

            Thin wrapper over the real ``RunContext.disallow_interruptions()``,
            which delegates to ``speech_handle.allow_interruptions = False``.
            Call this at the top of a tool function that must not be
            interrupted mid-execution.
            """
            run_ctx.disallow_interruptions()  # type: ignore[attr-defined]

else:  # livekit not installed — clear stub so the module still imports.

    class LiveKitLedgerAgent:  # type: ignore[no-redef]
        """Stub; livekit-agents is not installed. Install it to enable the m2 adapter."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
            raise ImportError(_LIVEKIT_INSTALL_HINT)


def attach_ledger(
    session: Any,
    ledger: TurnLedger,
    *,
    turn_id: str = "turn-lk-default",
    on_suspend: Callable[[LedgerEntry], None] | None = None,
    on_resume: Callable[[LedgerEntry | None], None] | None = None,
) -> Callable[[_LKOverlappingSpeechEvent], None]:
    """Wire a TurnLedger onto an existing LiveKit ``AgentSession``.

    For users who already have an ``Agent``/``AgentSession`` they don't want
    to subclass. Subscribes to the session's ``"overlapping_speech"`` event
    and calls ``ledger.suspend()``. (Resume still belongs to the Agent's
    ``on_user_turn_completed`` hook — use ``LiveKitLedgerAgent`` to get both
    sides wired automatically, or override the hook yourself and call
    ``ledger.resume(turn_id)``.)

    Returns the suspend handler so the caller can detach later if needed.
    """
    _require_livekit()

    def _handle(ev: _LKOverlappingSpeechEvent) -> None:  # type: ignore[valid-type]
        if not getattr(ev, "is_interruption", False):
            return
        cursor = TurnCursor(token_index=0, step_index=0, sub_step="voice:playout")
        entry = ledger.suspend(
            turn_id,
            cursor=cursor,
            payload={
                "adapter": "livekit",
                "agent_ended": getattr(ev, "agent_ended", False),
                "detected_at": getattr(ev, "detected_at", None),
            },
        )
        if on_suspend is not None:
            on_suspend(entry)

    session.on("overlapping_speech", _handle)
    return _handle


__all__ = ["LiveKitLedgerAgent", "attach_ledger"]
