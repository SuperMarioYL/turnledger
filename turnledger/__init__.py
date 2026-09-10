"""TurnLedger — an interruption-continuation ledger primitive for real-time agents.

When a user barges in mid-utterance, TurnLedger saves the agent's suspended
mid-step state and resumes *continuing* from the interrupted point after the
user finishes — instead of talking over the user or restarting the whole
turn from scratch.

Three verbs, one job: ``save`` / ``yield`` / ``resume-continue``.

Quick start:
    >>> from turnledger import InMemoryLedger, TurnCursor
    >>> ledger = InMemoryLedger()
    >>> ledger.suspend("turn-1", cursor=TurnCursor(token_index=7))
    LedgerEntry(turn_id='turn-1', seq=1, phase='yielding', ...)
    >>> entry = ledger.resume("turn-1")
    >>> entry.cursor.token_index
    7

See ``turnledger.protocol`` for the framework-agnostic Protocol, and
``turnledger.adapters`` for text-stream + LiveKit bindings.
"""

from __future__ import annotations

from .cursor import TurnCursor
from .ledger import (
    Ledger,
    LedgerEntry,
    InMemoryLedger,
    SQLiteLedger,
)
from .protocol import Store, TurnLedger
from .store import InMemoryStore, SQLiteStore

__version__ = "0.2.0"

__all__ = [
    "TurnCursor",
    "LedgerEntry",
    "Ledger",
    "InMemoryLedger",
    "SQLiteLedger",
    "TurnLedger",
    "Store",
    "InMemoryStore",
    "SQLiteStore",
    "__version__",
]
