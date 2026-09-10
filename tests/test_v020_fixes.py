"""v0.2.0 regression tests — demo CLI contracts + suspend-phase resumability.

Pins the two Phase-A bug fixes:

* ``turnledger.cli._print_ledger_audit`` crashed with
  ``AttributeError: 'LedgerEntry' object has no attribute 'step_index'``
  after ANY barge-in (it read ``entry.step_index``; the step lives at
  ``entry.cursor.step_index``), and ``--store`` silently ignored invalid
  values instead of raising a usage error.
* ``suspend(turn_id, phase="speaking"|"tool_call")`` — a documented Protocol
  feature — wrote a suspended entry that ``resume()`` could never replay:
  both stores keyed resumability on the phase label ``yielding`` instead of
  the suspended state (``yielded_at`` set, ``resumed_at`` unset).
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from turnledger import InMemoryLedger, SQLiteLedger, TurnCursor
from turnledger.cli import _print_ledger_audit, app

runner = CliRunner()


# -------------------------------------------------- m4: demo CLI contracts


class TestDemoAuditPrint:
    def test_audit_print_reads_step_from_cursor(self, capsys) -> None:
        ledger = InMemoryLedger()
        entry = ledger.suspend(
            "t1",
            cursor=TurnCursor(token_index=14, step_index=2, sub_step="llm:streaming"),
            payload={"adapter": "text_stream"},
        )
        # Pre-fix this raised AttributeError (entry has no step_index).
        _print_ledger_audit(entry, ledger, "t1")
        out = capsys.readouterr().out
        assert "step=2" in out
        assert "token#14" in out
        assert "llm:streaming" in out


class TestDemoEndToEnd:
    def test_demo_end_to_end_barge_in_exits_clean(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["demo", "--no-contrast", "-d", "0.005"], input="\nhello\n"
        )
        assert result.exit_code == 0, result.output
        assert "resume from token" in result.output
        assert "[ledger] cursor=" in result.output
        assert "Traceback" not in result.output

    def test_demo_end_to_end_sqlite_store(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["demo", "--no-contrast", "-d", "0.005", "--store", "sqlite"],
            input="\nhello\n",
        )
        assert result.exit_code == 0, result.output
        assert "[ledger] cursor=" in result.output

    def test_invalid_store_value_is_usage_error(self) -> None:
        result = runner.invoke(app, ["demo", "--no-contrast", "--store", "bogus"])
        assert result.exit_code == 2
        # The error names the allowed values (usage error, not a traceback).
        combined = result.output + getattr(result, "stderr", "")
        assert "memory" in combined and "sqlite" in combined
        assert "Traceback" not in combined


# ------------------------------------------- m5: suspend-phase resumability


@pytest.mark.parametrize("store", ["inmemory", "sqlite"])
class TestSuspendPhaseResumability:
    def _make_ledger(self, store: str, tmp_path) -> InMemoryLedger:
        if store == "sqlite":
            return SQLiteLedger(str(tmp_path / "ledger.db"))
        return InMemoryLedger()

    def test_suspend_with_provenance_phase_is_resumable(self, store, tmp_path) -> None:
        ledger = self._make_ledger(store, tmp_path)
        entry = ledger.suspend(
            "t1", cursor=TurnCursor(token_index=5), phase="speaking"
        )
        assert entry.is_suspended is True
        assert ledger.is_resumable("t1") is True
        resumed = ledger.resume("t1")
        assert resumed is not None
        assert resumed.phase == "resuming"
        assert resumed.cursor.token_index == 5
        assert [e.phase for e in ledger.history("t1")] == ["speaking", "resuming"]

    def test_suspend_with_tool_call_phase_resumes(self, store, tmp_path) -> None:
        ledger = self._make_ledger(store, tmp_path)
        ledger.suspend("t1", cursor=TurnCursor(token_index=7), phase="tool_call")
        assert ledger.is_resumable("t1") is True
        resumed = ledger.resume("t1")
        assert resumed is not None
        assert resumed.cursor.token_index == 7
        assert [e.phase for e in ledger.history("t1")] == ["tool_call", "resuming"]

    def test_resume_flips_suspended_source_entry(self, store, tmp_path) -> None:
        ledger = self._make_ledger(store, tmp_path)
        ledger.suspend("t1", cursor=TurnCursor(token_index=5), phase="speaking")
        ledger.resume("t1")
        source = ledger.history("t1")[0]
        assert source.resumed_at is not None
        assert ledger.is_resumable("t1") is False

    def test_default_yielding_phase_unchanged(self, store, tmp_path) -> None:
        ledger = self._make_ledger(store, tmp_path)
        ledger.suspend("t1", cursor=TurnCursor(token_index=3))
        resumed = ledger.resume("t1")
        assert resumed is not None
        history = ledger.history("t1")
        assert [e.phase for e in history] == ["yielding", "resuming"]
        assert [e.seq for e in history] == [1, 2]
