"""LedgerEntry + the concrete Ledger that implements the save/yield/resume Protocol.

This is the core primitive (mvp_plan §2). Everything else — adapters, CLI,
LiveKit wiring — is a thin layer over the three verbs defined here.

Design notes:

* **Append-only.** Every ``suspend`` and every ``resume`` writes a new
  ``LedgerEntry``. The full interruption history of a turn is always
  recoverable, which is what makes the ledger double as an audit log
  (the Cloud tier observability surface builds on this).
* **Sync core, async-friendly.** The verbs are synchronous and
  microsecond-fast for the in-memory store; LiveKit's async adapter calls
  them directly (they never block the event loop on network I/O). The
  SQLite store is local-disk and equally fast for the volumes a single
  agent produces.
* **Framework-agnostic.** Nothing here imports LiveKit, pipecat, or dify.
  An adapter owns cursor + payload; the ledger only stores and replays.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from .cursor import TurnCursor
from .protocol import Store

Phase = Literal["speaking", "tool_call", "yielding", "resuming"]
"""The four phases a ledger entry can stamp.

``speaking`` / ``tool_call`` record what was interrupted; ``yielding`` is the
suspend event itself; ``resuming`` is the resume event. An adapter may pass
any phase to ``suspend`` to record provenance; ``resume`` always stamps
``"resuming"``.
"""


class LedgerEntry(BaseModel):
    """One record in the interruption-continuation log.

    Attributes:
        turn_id: The in-flight turn this entry belongs to.
        seq: Monotonic per-turn sequence number (1-based).
        phase: One of ``speaking`` / ``tool_call`` / ``yielding`` /
            ``resuming``.
        cursor: Where in the turn we were (token_index / step_index /
            sub_step).
        payload: Suspended mid-step state: partial tool args, buffered
            tokens, pending commits — whatever the adapter needs to
            restore to continue from the interrupted point.
        yielded_at: Wall-clock seconds when the floor was yielded
            (set on ``suspend``). ``None`` until suspend.
        resumed_at: Wall-clock seconds when the turn was resumed
            (set on ``resume``). ``None`` until resume.
    """

    turn_id: str
    seq: int = Field(ge=1)
    phase: Phase
    cursor: TurnCursor = Field(default_factory=TurnCursor)
    payload: dict[str, Any] = Field(default_factory=dict)
    yielded_at: float | None = None
    resumed_at: float | None = None

    @property
    def is_suspended(self) -> bool:
        """True if this entry captured state and has not been resumed yet."""
        return self.yielded_at is not None and self.resumed_at is None

    @property
    def is_resumed(self) -> bool:
        return self.resumed_at is not None


class Ledger:
    """The in-process interruption-continuation ledger.

    Implements the ``TurnLedger`` Protocol (see ``turnledger.protocol``) by
    delegating persistence to a pluggable ``Store``. This is the class users
    instantiate directly; for the one-liner happy path use the convenience
    subclasses ``InMemoryLedger`` / ``SQLiteLedger``.

    Example:
        >>> from turnledger import InMemoryLedger, TurnCursor
        >>> ledger = InMemoryLedger()
        >>> ledger.suspend("t1", cursor=TurnCursor(token_index=12))
        LedgerEntry(turn_id='t1', seq=1, phase='yielding', ...)
        >>> entry = ledger.resume("t1")
        >>> entry.cursor.token_index
        12
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        # The last cursor+payload the adapter reported, for the common case
        # where suspend() is called with no explicit cursor (mvp_plan §3).
        self._last_cursor: dict[str, TurnCursor] = {}
        self._last_payload: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API

    def report(self, turn_id: str, *, cursor: TurnCursor, payload: dict | None = None) -> None:
        """Let the adapter stream its current cursor+payload into the ledger.

        Adapters call this on every token / chunk / step boundary so that a
        bare ``suspend(turn_id)`` (no args) captures the freshest position.
        Cheap: just two dict writes under the lock.
        """
        with self._lock:
            self._last_cursor[turn_id] = cursor
            if payload is not None:
                self._last_payload[turn_id] = dict(payload)

    def suspend(
        self,
        turn_id: str,
        *,
        cursor: TurnCursor | None = None,
        payload: dict | None = None,
        phase: Phase = "yielding",
    ) -> LedgerEntry:
        """Capture suspended mid-step state, yield the floor. Append-only."""
        with self._lock:
            if cursor is None:
                cursor = self._last_cursor.get(turn_id, TurnCursor())
            if payload is None:
                payload = dict(self._last_payload.get(turn_id, {}))
            else:
                payload = dict(payload)
                self._last_payload[turn_id] = payload
            self._last_cursor[turn_id] = cursor

            seq = self._store.next_seq(turn_id)
            entry = LedgerEntry(
                turn_id=turn_id,
                seq=seq,
                phase=phase,
                cursor=cursor,
                payload=payload,
                yielded_at=time.time(),
                resumed_at=None,
            )
            self._store.save(entry)
            return entry

    def resume(self, turn_id: str) -> LedgerEntry | None:
        """Replay the latest suspended entry, mark resumed. Append-only."""
        with self._lock:
            latest = self._store.latest_yielding_for(turn_id)
            if latest is None:
                return None
            seq = self._store.next_seq(turn_id)
            now = time.time()
            resumed = LedgerEntry(
                turn_id=turn_id,
                seq=seq,
                phase="resuming",
                cursor=latest.cursor,
                payload=dict(latest.payload),
                yielded_at=latest.yielded_at,
                resumed_at=now,
            )
            self._store.save(resumed)
            # Mark the source yielding entry as resumed (status flip, not a
            # log append) so latest_yielding_for stops returning it and
            # is_resumable() correctly reports False after a resume.
            self._store.mark_resumed(turn_id, latest.seq, now)
            # The adapter continues from here; keep the cursor as the last
            # reported so a subsequent suspend() without args still works.
            self._last_cursor[turn_id] = resumed.cursor
            self._last_payload[turn_id] = dict(resumed.payload)
            return resumed

    def is_resumable(self, turn_id: str) -> bool:
        """True if the turn has an un-resumed suspended entry."""
        return self._store.latest_yielding_for(turn_id) is not None

    # ----------------------------------------------------------- read-throughs

    def latest(self, turn_id: str) -> LedgerEntry | None:
        """The most recent entry for ``turn_id`` of any phase."""
        return self._store.latest_for(turn_id)

    def history(self, turn_id: str) -> list[LedgerEntry]:
        """The full append-order history of a turn (audit log)."""
        return self._store.entries_for(turn_id)

    def clear(self) -> None:
        with self._lock:
            self._last_cursor.clear()
            self._last_payload.clear()
            self._store.clear()


class InMemoryLedger(Ledger):
    """A ``Ledger`` backed by an in-process store. The default happy path."""

    def __init__(self) -> None:
        from .store import InMemoryStore

        super().__init__(InMemoryStore())


class SQLiteLedger(Ledger):
    """A ``Ledger`` backed by a local SQLite file (durable / audit log).

    Args:
        path: SQLite database path. ``":memory:"`` for an ephemeral in-process
            DB (useful for tests); a filesystem path for persistence across
            restarts. The Cloud tier (roadmap) swaps this store for a remote
            one with the same ABC.
    """

    def __init__(self, path: str = "turnledger.db") -> None:
        from .store import SQLiteStore

        super().__init__(SQLiteStore(path))


__all__ = ["Phase", "LedgerEntry", "Ledger", "InMemoryLedger", "SQLiteLedger"]
