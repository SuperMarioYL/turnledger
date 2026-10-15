"""v0.3.0 tests — unreadable-db clean errors, NULL-timestamp render, --token-delay validation.

Regression tests for three empirically-repro'd crash families at v0.2.0
(each verified with the installed CLI, raw traceback + exit 1):

1. ``turnledger history`` (and ``demo --store sqlite``) leaked raw
   sqlite3.DatabaseError / OperationalError / pydantic ValidationError
   tracebacks on unreadable or corrupt ledger dbs, although the command
   already owns a clean-error contract (``error: ...``, exit 2) for a
   missing db.
2. ``turnledger history`` crashed with ``TypeError`` rendering a
   schema-valid row whose ``yielded_at`` is NULL (the Store ABC is a
   public seam; store-level writes persist such rows verbatim).
3. ``turnledger demo --token-delay <negative>`` crashed with
   ``ValueError: sleep length must be non-negative`` inside the
   streaming loop.
"""

from __future__ import annotations

import json
import sqlite3

from pydantic import ValidationError  # noqa: F401  (re-exported for fix readability)
from typer.testing import CliRunner

from turnledger import SQLiteLedger, SQLiteStore, TurnCursor
from turnledger.cli import app
from turnledger.ledger import LedgerEntry

runner = CliRunner()


def _seed_db(tmp_path, turn_id: str = "turn-a") -> str:
    """Write one suspend + resume into a durable SQLite ledger, return path."""
    db = tmp_path / "ledger.db"
    ledger = SQLiteLedger(str(db))
    ledger.suspend(
        turn_id,
        cursor=TurnCursor(token_index=12, step_index=1, sub_step="llm:streaming"),
        payload={"adapter": "text_stream"},
    )
    ledger.resume(turn_id)
    return str(db)


def _combined(result) -> str:
    return result.output + (getattr(result, "stderr", "") or "")


class TestHistoryUnreadableDb:
    def test_history_non_sqlite_file_is_clean_error(self, tmp_path) -> None:
        bad = tmp_path / "notadb.db"
        bad.write_text("this is not a sqlite database\n")
        result = runner.invoke(app, ["history", "turn-a", "--db", str(bad)])
        assert result.exit_code == 2, result.output
        assert "error" in _combined(result).lower()
        assert "Traceback" not in _combined(result)

    def test_history_directory_db_is_clean_error(self, tmp_path) -> None:
        result = runner.invoke(app, ["history", "turn-a", "--db", str(tmp_path)])
        assert result.exit_code == 2, result.output
        assert "error" in _combined(result).lower()
        assert "Traceback" not in _combined(result)

    def test_history_corrupt_row_is_clean_error(self, tmp_path) -> None:
        db = tmp_path / "corrupt.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE ledger_entries (
                turn_id TEXT NOT NULL, seq INTEGER NOT NULL, phase TEXT NOT NULL,
                cursor TEXT NOT NULL, payload TEXT NOT NULL, yielded_at REAL,
                resumed_at REAL, PRIMARY KEY (turn_id, seq));
            """
        )
        conn.execute(
            "INSERT INTO ledger_entries VALUES ('t1',1,'yielding','{not-json','{}',123.4,NULL)"
        )
        conn.commit()
        conn.close()
        result = runner.invoke(app, ["history", "t1", "--db", str(db)])
        assert result.exit_code == 2, result.output
        assert "error" in _combined(result).lower()
        assert "Traceback" not in _combined(result)

    def test_demo_sqlite_corrupt_db_is_clean_error(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "turnledger.db").write_text("garbage not a db\n")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["demo", "--no-contrast", "-d", "0.001", "--store", "sqlite"], input=""
        )
        assert result.exit_code == 2, result.output
        assert "error" in _combined(result).lower()
        assert "Traceback" not in _combined(result)

    def test_history_valid_db_still_renders(self, tmp_path) -> None:
        db = _seed_db(tmp_path)
        result = runner.invoke(app, ["history", "turn-a", "--db", db])
        assert result.exit_code == 0, result.output
        assert "yielding" in result.output
        assert "resuming" in result.output
        assert "token#12" in result.output
        assert "Traceback" not in result.output


class TestHistoryNullTimestampRender:
    def test_history_null_yielded_at_renders(self, tmp_path) -> None:
        db = tmp_path / "null.db"
        # Store-level write through the public ABC: LedgerEntry.yielded_at
        # defaults to None and the schema column is nullable, so this row is
        # schema-valid ledger state.
        store = SQLiteStore(str(db))
        store.save(
            LedgerEntry(
                turn_id="t1",
                seq=1,
                phase="speaking",
                cursor=TurnCursor(token_index=3),
            )
        )
        store.save(
            LedgerEntry(
                turn_id="t1",
                seq=2,
                phase="yielding",
                cursor=TurnCursor(token_index=3),
                yielded_at=123.456,
            )
        )
        store.close()
        result = runner.invoke(app, ["history", "t1", "--db", str(db)])
        assert result.exit_code == 0, result.output
        assert "Traceback" not in _combined(result)
        assert "[1] speaking" in result.output
        assert "[2] yielding" in result.output
        assert "123.456" in result.output

    def test_history_json_preserves_null_yielded_at(self, tmp_path) -> None:
        db = tmp_path / "null.db"
        store = SQLiteStore(str(db))
        store.save(
            LedgerEntry(
                turn_id="t1",
                seq=1,
                phase="speaking",
                cursor=TurnCursor(token_index=3),
            )
        )
        store.close()
        result = runner.invoke(app, ["history", "t1", "--db", str(db), "--json"])
        assert result.exit_code == 0, result.output
        entries = json.loads(result.output)
        assert entries[0]["yielded_at"] is None


class TestTokenDelayValidation:
    def test_negative_token_delay_is_usage_error(self) -> None:
        result = runner.invoke(app, ["demo", "--no-contrast", "-d", "-0.5"], input="")
        assert result.exit_code == 2, result.output
        assert "Traceback" not in _combined(result)
        # The rendered error panel is rich-formatted and GH runners force
        # ANSI + elide the panel at their terminal width, so pin the message
        # on the raw exception (standalone_mode=False) instead of the output.
        raw = runner.invoke(
            app, ["demo", "--no-contrast", "-d", "-0.5"], input="", standalone_mode=False
        )
        assert raw.exception is not None
        assert "token delay must be >= 0" in str(raw.exception)

    def test_zero_token_delay_still_runs(self, tmp_path) -> None:
        result = runner.invoke(app, ["demo", "--no-contrast", "-d", "0"], input="")
        assert result.exit_code == 0, result.output
        assert "[ledger] turn=" in result.output
        assert "Traceback" not in result.output
