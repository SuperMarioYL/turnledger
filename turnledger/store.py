"""Store backends for the ledger.

Two implementations of ``turnledger.protocol.Store``:

* ``InMemoryStore`` — the default, in-process dict. Fast, no deps, lost on
  exit. Used by ``InMemoryLedger`` and the ``turnledger demo`` CLI.
* ``SQLiteStore`` — a local SQLite file. Durable across restarts, queryable
  for audit, the OSS ceiling before the Cloud tier. Used by
  ``SQLiteLedger``.

Both are thread-safe (a lock / per-connection serialization). The ledger
itself is sync; LiveKit's async adapter calls these directly because they
never block on network I/O — only local dict or local-disk ops.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .cursor import TurnCursor
from .ledger import LedgerEntry, Phase


class InMemoryStore:
    """In-process dict-backed store. Default; lost on process exit."""

    def __init__(self) -> None:
        self._entries: dict[str, list[LedgerEntry]] = {}
        self._lock = threading.Lock()

    def save(self, entry: LedgerEntry) -> None:
        with self._lock:
            self._entries.setdefault(entry.turn_id, []).append(entry)

    def latest_for(self, turn_id: str) -> LedgerEntry | None:
        with self._lock:
            buf = self._entries.get(turn_id)
            return buf[-1] if buf else None

    def latest_yielding_for(self, turn_id: str) -> LedgerEntry | None:
        with self._lock:
            for entry in reversed(self._entries.get(turn_id, ())):
                # A suspend is resumable by STATE (yielded and not yet
                # resumed), not by phase label: suspend() may stamp
                # "speaking"/"tool_call" provenance phases (LedgerEntry.
                # is_suspended defines the same criterion).
                if entry.yielded_at is not None and entry.resumed_at is None:
                    return entry
            return None

    def mark_resumed(self, turn_id: str, seq: int, resumed_at: float) -> None:
        with self._lock:
            for entry in reversed(self._entries.get(turn_id, ())):
                if entry.seq == seq and entry.resumed_at is None:
                    entry.resumed_at = resumed_at
                    return

    def entries_for(self, turn_id: str) -> list[LedgerEntry]:
        with self._lock:
            return list(self._entries.get(turn_id, ()))

    def next_seq(self, turn_id: str) -> int:
        with self._lock:
            buf = self._entries.get(turn_id)
            return (buf[-1].seq + 1) if buf else 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class SQLiteStore:
    """SQLite-backed store. Durable; queryable; the OSS audit-log ceiling.

    Schema is a single ``ledger_entries`` table. ``cursor`` and ``payload``
    are JSON columns (the pydantic models round-trip cleanly). The store
    uses ``check_same_thread=False`` + a lock so an async LiveKit adapter
    can call it from any thread the event loop schedules.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS ledger_entries (
        turn_id     TEXT    NOT NULL,
        seq         INTEGER NOT NULL,
        phase       TEXT    NOT NULL,
        cursor      TEXT    NOT NULL,
        payload     TEXT    NOT NULL,
        yielded_at  REAL,
        resumed_at  REAL,
        PRIMARY KEY (turn_id, seq)
    );
    CREATE INDEX IF NOT EXISTS idx_ledger_turn ON ledger_entries(turn_id);
    """

    def __init__(self, path: str = "turnledger.db") -> None:
        self._path = path
        parent = Path(path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row_to_entry(self, row: sqlite3.Row) -> LedgerEntry:
        return LedgerEntry(
            turn_id=row["turn_id"],
            seq=row["seq"],
            phase=row["phase"],
            cursor=TurnCursor.model_validate_json(row["cursor"]),
            payload=json.loads(row["payload"]) if row["payload"] else {},
            yielded_at=row["yielded_at"],
            resumed_at=row["resumed_at"],
        )

    def save(self, entry: LedgerEntry) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO ledger_entries "
                "(turn_id, seq, phase, cursor, payload, yielded_at, resumed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.turn_id,
                    entry.seq,
                    entry.phase,
                    entry.cursor.model_dump_json(),
                    json.dumps(entry.payload),
                    entry.yielded_at,
                    entry.resumed_at,
                ),
            )
            self._conn.commit()

    def latest_for(self, turn_id: str) -> LedgerEntry | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM ledger_entries WHERE turn_id = ? "
                "ORDER BY seq DESC LIMIT 1",
                (turn_id,),
            )
            row = cur.fetchone()
            return self._row_to_entry(row) if row else None

    def latest_yielding_for(self, turn_id: str) -> LedgerEntry | None:
        with self._lock:
            # Resumable by STATE (yielded and not yet resumed), not by phase
            # label — suspend() may stamp "speaking"/"tool_call" provenance
            # phases; "resuming" rows stay excluded (resumed_at is set).
            cur = self._conn.execute(
                "SELECT * FROM ledger_entries "
                "WHERE turn_id = ? AND yielded_at IS NOT NULL AND resumed_at IS NULL "
                "ORDER BY seq DESC LIMIT 1",
                (turn_id,),
            )
            row = cur.fetchone()
            return self._row_to_entry(row) if row else None

    def mark_resumed(self, turn_id: str, seq: int, resumed_at: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE ledger_entries SET resumed_at = ? "
                "WHERE turn_id = ? AND seq = ? AND resumed_at IS NULL",
                (resumed_at, turn_id, seq),
            )
            self._conn.commit()

    def entries_for(self, turn_id: str) -> list[LedgerEntry]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM ledger_entries WHERE turn_id = ? ORDER BY seq ASC",
                (turn_id,),
            )
            return [self._row_to_entry(r) for r in cur.fetchall()]

    def next_seq(self, turn_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM ledger_entries WHERE turn_id = ?",
                (turn_id,),
            )
            return int(cur.fetchone()[0])

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM ledger_entries")
            self._conn.commit()


__all__ = ["InMemoryStore", "SQLiteStore"]
