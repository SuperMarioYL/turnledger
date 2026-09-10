"""v0.2.0 tests — the ``turnledger history`` CLI command.

Promotes the existing ``Ledger.history()`` / ``SQLiteStore.entries_for``
audit-log API to a read-only CLI command against a pre-filled durable
ledger.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from turnledger import SQLiteLedger, TurnCursor
from turnledger.cli import app

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


class TestHistoryCommand:
    def test_history_prints_audit_log(self, tmp_path) -> None:
        db = _seed_db(tmp_path)
        result = runner.invoke(app, ["history", "turn-a", "--db", db])
        assert result.exit_code == 0, result.output
        assert "turn-a" in result.output
        assert "yielding" in result.output
        assert "resuming" in result.output
        assert "token#12" in result.output
        assert "Traceback" not in result.output

    def test_history_json_round_trips(self, tmp_path) -> None:
        db = _seed_db(tmp_path)
        result = runner.invoke(app, ["history", "turn-a", "--db", db, "--json"])
        assert result.exit_code == 0, result.output
        entries = json.loads(result.output)
        assert [e["seq"] for e in entries] == [1, 2]
        assert [e["phase"] for e in entries] == ["yielding", "resuming"]
        assert entries[0]["cursor"]["token_index"] == 12
        assert entries[0]["cursor"]["sub_step"] == "llm:streaming"

    def test_history_missing_db_is_clean_error(self, tmp_path) -> None:
        result = runner.invoke(
            app, ["history", "turn-a", "--db", str(tmp_path / "nope.db")]
        )
        assert result.exit_code == 2
        combined = result.output + getattr(result, "stderr", "")
        assert "not found" in combined
        assert "Traceback" not in combined

    def test_history_empty_turn(self, tmp_path) -> None:
        db = _seed_db(tmp_path)
        result = runner.invoke(app, ["history", "unknown-turn", "--db", db])
        assert result.exit_code == 0, result.output
        assert "no entries" in result.output
