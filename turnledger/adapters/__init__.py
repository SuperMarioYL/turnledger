"""Framework adapters for TurnLedger.

Each adapter binds the ledger's ``suspend`` / ``resume`` verbs to a specific
agent framework's barge-in surface. The ledger stays framework-agnostic;
the adapter owns the mapping.

* ``text_stream`` (m1) — a minimal streaming-text agent whose "interrupt"
  surface is a keyboard Enter. Powers ``turnledger demo``.
* ``livekit`` (m2, ship target) — binds LiveKit ``OverlappingSpeechEvent``
  → ``ledger.suspend()`` and ``Agent.on_user_turn_completed`` →
  ``ledger.resume()`` on an ``Agent`` configured with
  ``allow_interruptions=True`` + ``interruption_detection``.
"""

from __future__ import annotations

from .text_stream import TextStreamAgent, TextBargeInSimulator

__all__ = ["TextStreamAgent", "TextBargeInSimulator"]

# livekit adapter is imported lazily — livekit-agents is a heavy optional dep
# only needed for the m2 voice demo. ``from turnledger.adapters.livekit import
# LiveKitLedgerAgent`` will raise a clear ImportError if the package is absent.
