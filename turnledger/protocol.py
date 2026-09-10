"""Protocol layer — the save/yield/resume interface + the Store ABC.

This module defines the two contracts the rest of the package is built on:

* ``TurnLedger`` — the save/yield/resume primitive every adapter binds to.
  It is a ``typing.Protocol`` so any object with the right methods satisfies
  it; the concrete implementation lives in ``turnledger.ledger``.
* ``Store`` — the pluggable persistence ABC (``InMemoryStore`` default,
  ``SQLiteStore`` optional). Swap the store to take the ledger from a
  process-local demo to a durable audit log.

Nothing here depends on LiveKit, pipecat, or any agent framework — that is
the point. The handshake is framework-agnostic by design (mvp_plan §4 / m3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # LedgerEntry / TurnCursor are referenced only in annotations (kept as
    # strings by `from __future__ import annotations`), so importing them
    # under TYPE_CHECKING avoids a runtime cycle between protocol <-> ledger.
    from .cursor import TurnCursor
    from .ledger import LedgerEntry


@runtime_checkable
class TurnLedger(Protocol):
    """The interruption-continuation ledger primitive.

    Three verbs, one job: let a real-time agent save its suspended mid-step
    state, yield the floor to the user, and resume *continuing* from the
    interrupted point once the user finishes — instead of aborting and
    restarting the whole turn.

    Implementations are append-only: every ``suspend`` and every ``resume``
    writes a ``LedgerEntry`` so the full interruption history of a turn is
    recoverable for audit (this is also what the Cloud tier observability
    surface is built on).
    """

    def suspend(
        self,
        turn_id: str,
        *,
        cursor: TurnCursor | None = None,
        payload: dict | None = None,
        phase: str = "yielding",
    ) -> LedgerEntry:
        """Capture the agent's suspended mid-step state and yield the floor.

        Args:
            turn_id: The in-flight turn to suspend.
            cursor: Where in the turn we were. ``None`` means "the agent's
                last reported cursor" (the ledger remembers it).
            payload: Suspended mid-step state — partial tool args, buffered
                tokens, pending commits. ``None`` means reuse the last
                payload.
            phase: Phase label to stamp on the entry. Defaults to
                ``"yielding"``; an adapter may pass ``"speaking"`` or
                ``"tool_call"`` to record what was interrupted.

        Returns:
            The freshly appended ``LedgerEntry`` with ``yielded_at`` set
            and ``resumed_at`` left ``None``.
        """
        ...

    def resume(self, turn_id: str) -> LedgerEntry | None:
        """Replay the latest suspended entry and mark the turn resumed.

        Returns:
            A new ``LedgerEntry`` with ``phase == "resuming"`` whose
            ``cursor`` and ``payload`` are copied from the latest un-resumed
            ``yielding`` entry, or ``None`` if the turn has nothing to resume.
        """
        ...

    def is_resumable(self, turn_id: str) -> bool:
        """True if the turn has a suspended entry that has not been resumed."""
        ...


class Store(ABC):
    """Pluggable persistence for the ledger.

    The ledger itself is framework-agnostic and process-local in v0.1; the
    store is the seam where durability is added. ``InMemoryStore`` is the
    default; ``SQLiteStore`` is the optional durable / audit-log backend;
    the Cloud tier (roadmap) swaps in a remote store with the same ABC.
    """

    @abstractmethod
    def save(self, entry: LedgerEntry) -> None:
        """Append a ``LedgerEntry`` to the log."""
        ...

    @abstractmethod
    def latest_for(self, turn_id: str) -> LedgerEntry | None:
        """The most recent entry for ``turn_id``, or ``None`` if none."""
        ...

    @abstractmethod
    def latest_yielding_for(self, turn_id: str) -> LedgerEntry | None:
        """The most recent un-resumed suspended entry, or ``None``.

        Suspended is a STATE (``yielded_at`` set and ``resumed_at`` unset),
        not a phase label: ``suspend()`` may stamp ``"speaking"`` /
        ``"tool_call"`` provenance phases, and those suspends are resumable
        exactly like default ``"yielding"`` ones.
        """
        ...

    @abstractmethod
    def mark_resumed(self, turn_id: str, seq: int, resumed_at: float) -> None:
        """Stamp ``resumed_at`` on the given yielding entry (mark, not append).

        ``resume()`` appends a new ``resuming`` entry (the append-only log)
        AND flips this status field on the source ``yielding`` entry so
        ``latest_yielding_for`` stops returning it. The mark is a status
        update, not a log append — consistent with the spec's "mark resumed".
        """
        ...

    @abstractmethod
    def entries_for(self, turn_id: str) -> list[LedgerEntry]:
        """All entries for ``turn_id`` in append order (oldest first)."""
        ...

    @abstractmethod
    def next_seq(self, turn_id: str) -> int:
        """The next monotonic ``seq`` for ``turn_id`` (1-based)."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Drop all entries (test / reset hook)."""
        ...
