"""v0.4.0 tests — ledger-read contracts, JSON output shape, delay validation, audit label.

Regression tests for four defects reproduced on the shipped v0.3.0 CLI plus one
output-label quality fix:

1. ``turnledger history`` leaked a raw ``json.JSONDecodeError`` traceback when a
   row's ``payload`` column was not JSON (the v0.3.0 clean-error conversion only
   caught pydantic ``ValidationError`` from the ``cursor`` column).
2. ``turnledger history`` — a documented read of the audit log — created a
   ``ledger_entries`` table inside whatever SQLite file it was pointed at, and
   turned a 0-byte file into a database, because it built a write-mode
   ``SQLiteStore`` just to read one turn.
3. ``turnledger history --json`` printed prose instead of a JSON list for a turn
   with no entries, breaking the documented machine-readable shape.
4. ``turnledger demo --token-delay nan|inf`` passed the ``< 0`` guard and
   crashed inside ``time.sleep`` with ``ValueError`` / ``OverflowError``.
5. The demo audit line labelled the turn's whole log length as
   ``suspended_entries`` (one barge-in printed 2: yielding + resuming).
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from typer.testing import CliRunner

from turnledger import SQLiteLedger, TurnCursor
from turnledger.cli import app

runner = CliRunner()

SCHEMA = """
CREATE TABLE ledger_entries (
    turn_id TEXT NOT NULL, seq INTEGER NOT NULL, phase TEXT NOT NULL,
    cursor TEXT NOT NULL, payload TEXT NOT NULL, yielded_at REAL,
    resumed_at REAL, PRIMARY KEY (turn_id, seq));
"""


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


class TestHistoryCorruptPayload:
    def test_history_corrupt_payload_is_clean_error(self, tmp_path) -> None:
        db = tmp_path / "payload.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO ledger_entries VALUES "
            """('t1',1,'yielding','{"token_index": 3}','{not-json',123.4,NULL)"""
        )
        conn.commit()
        conn.close()

        result = runner.invoke(app, ["history", "t1", "--db", str(db)])
        assert result.exit_code == 2, result.output
        assert "error" in _combined(result).lower()
        assert "Traceback" not in _combined(result)


class TestHistoryReadOnly:
    def test_history_does_not_mutate_foreign_db(self, tmp_path) -> None:
        db = tmp_path / "foreign.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users (name) VALUES ('alice')")
        conn.commit()
        conn.close()

        result = runner.invoke(app, ["history", "turn-a", "--db", str(db)])
        assert result.exit_code == 2, result.output
        assert "error" in _combined(result).lower()
        assert "Traceback" not in _combined(result)

        tables = [
            row[0]
            for row in sqlite3.connect(str(db)).execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        assert tables == ["users"]

    def test_history_does_not_create_empty_db(self, tmp_path) -> None:
        db = tmp_path / "empty.db"
        db.write_bytes(b"")

        result = runner.invoke(app, ["history", "turn-a", "--db", str(db)])
        assert result.exit_code == 2, result.output
        assert "error" in _combined(result).lower()
        assert db.stat().st_size == 0

    def test_history_readonly_still_renders_seeded_db(self, tmp_path) -> None:
        db = _seed_db(tmp_path)
        result = runner.invoke(app, ["history", "turn-a", "--db", db])
        assert result.exit_code == 0, result.output
        assert "yielding" in result.output
        assert "resuming" in result.output
        assert "token#12" in result.output
        assert "Traceback" not in result.output


class TestHistoryJsonShape:
    def test_history_json_empty_turn_is_json(self, tmp_path) -> None:
        db = _seed_db(tmp_path)
        result = runner.invoke(app, ["history", "unknown-turn", "--db", db, "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []


class TestTokenDelayValidation:
    @pytest.mark.parametrize("value", ["nan", "inf"])
    def test_nonfinite_token_delay_is_usage_error(self, value: str) -> None:
        result = runner.invoke(
            app, ["demo", "--no-contrast", "-d", value], input=""
        )
        assert result.exit_code == 2, result.output
        assert "Traceback" not in _combined(result)
        raw = runner.invoke(
            app, ["demo", "--no-contrast", "-d", value], input="", standalone_mode=False
        )
        assert raw.exception is not None
        assert "token delay must be >= 0" in str(raw.exception)
        assert getattr(raw.exception, "param_hint", None) == "--token-delay"

    def test_finite_token_delays_still_run(self) -> None:
        result = runner.invoke(app, ["demo", "--no-contrast", "-d", "0"], input="")
        assert result.exit_code == 0, result.output
        assert "[ledger] turn=" in result.output


class TestDemoAuditLabel:
    def test_demo_audit_line_reports_entry_count(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["demo", "--no-contrast", "-d", "0.005"], input="\nhello\n"
        )
        assert result.exit_code == 0, result.output
        assert "[ledger] cursor=" in result.output
        assert "entries=" in result.output
        assert "suspended_entries=" not in result.output
        assert "Traceback" not in result.output
