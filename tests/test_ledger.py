"""Tests for the TurnLedger primitive — m1 (save/yield/resume) + m2 (adapters).

Coverage:
  * TurnCursor shape + immutable advances (cursor.py)
  * LedgerEntry phase transitions + is_suspended/is_resumed (ledger.py)
  * InMemoryStore + SQLiteStore parity (store.py)
  * Ledger save/yield/resume handshake, append-only audit, multi-turn (ledger.py)
  * TextStreamAgent + TextBargeInSimulator contrast (m1 adapter)
  * LiveKit adapter wiring against the real API surface (m2 adapter) —
    verified via a lightweight fake session/handle so the test does not
    require a livekit room, and via attribute-name guards so a real
    livekit-agents install keeps the adapter honest.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any
from unittest import mock

import pytest

from turnledger import (
    InMemoryLedger,
    InMemoryStore,
    SQLiteLedger,
    SQLiteStore,
    TurnCursor,
    LedgerEntry,
)
from turnledger.adapters.text_stream import TextBargeInSimulator, TextStreamAgent
from turnledger.ledger import Ledger
from turnledger.protocol import Store, TurnLedger


def _oneshot(at: int):
    """A barge-in predicate that fires exactly once at token index ``at``.

    Mirrors the real CLI's keyboard listener, which clears its interrupt flag
    after ``on_yield`` consumes it — so the same barge-in is not re-detected
    on the very next iteration (which would loop forever, since resume()
    sets the cursor back to the interrupted index). Tests must use a one-shot
    predicate per the adapter contract.
    """
    fired = {"v": False}

    def _pred(i: int, _t: str) -> bool:
        if fired["v"]:
            return False
        if i == at:
            fired["v"] = True
            return True
        return False

    return _pred


# ----------------------------------------------------------------- TurnCursor


class TestTurnCursor:
    def test_defaults(self) -> None:
        c = TurnCursor()
        assert c.token_index == 0
        assert c.step_index == 0
        assert c.sub_step == ""

    def test_advance_token_returns_new_instance(self) -> None:
        c = TurnCursor(token_index=5, step_index=1, sub_step="llm:streaming")
        c2 = c.advance_token()
        assert c.token_index == 5  # immutable: original unchanged
        assert c2.token_index == 6
        assert c2.sub_step == "llm:streaming"

    def test_with_step_and_sub_step(self) -> None:
        c = TurnCursor(token_index=3, step_index=0, sub_step="a")
        c2 = c.with_step(2, "tool_call:args")
        assert c2.step_index == 2
        assert c2.sub_step == "tool_call:args"
        assert c2.token_index == 3

    def test_rejects_negative_index(self) -> None:
        with pytest.raises(Exception):
            TurnCursor(token_index=-1)


# --------------------------------------------------------------- LedgerEntry


class TestLedgerEntry:
    def test_phases(self) -> None:
        e = LedgerEntry(turn_id="t", seq=1, phase="yielding")
        assert e.yielded_at is None  # not yet stamped by the ledger
        assert not e.is_suspended

    def test_is_suspended_and_resumed(self) -> None:
        e = LedgerEntry(
            turn_id="t", seq=1, phase="yielding", yielded_at=1.0, resumed_at=None
        )
        assert e.is_suspended
        assert not e.is_resumed
        e2 = LedgerEntry(
            turn_id="t", seq=2, phase="resuming", yielded_at=1.0, resumed_at=2.0
        )
        assert e2.is_resumed
        assert not e2.is_suspended

    def test_payload_defaults_to_dict(self) -> None:
        e = LedgerEntry(turn_id="t", seq=1, phase="yielding")
        assert e.payload == {}


# -------------------------------------------------------------------- Stores


class TestInMemoryStore:
    def test_save_and_latest(self) -> None:
        s = InMemoryStore()
        e1 = LedgerEntry(turn_id="t", seq=1, phase="yielding", yielded_at=1.0)
        e2 = LedgerEntry(turn_id="t", seq=2, phase="resuming", yielded_at=1.0, resumed_at=2.0)
        s.save(e1)
        s.save(e2)
        # latest_for returns the most recent entry of any phase.
        assert s.latest_for("t").seq == 2
        # latest_yielding_for matches phase=="yielding" AND resumed_at IS NULL.
        # e1 qualifies (resumed_at is None); e2 is phase=resuming so skipped.
        latest = s.latest_yielding_for("t")
        assert latest is not None
        assert latest.seq == 1

    def test_next_seq_monotonic(self) -> None:
        s = InMemoryStore()
        assert s.next_seq("t") == 1
        s.save(LedgerEntry(turn_id="t", seq=1, phase="yielding", yielded_at=1.0))
        assert s.next_seq("t") == 2
        assert s.next_seq("other") == 1

    def test_entries_for_append_order(self) -> None:
        s = InMemoryStore()
        for i in range(3):
            s.save(LedgerEntry(turn_id="t", seq=i + 1, phase="yielding", yielded_at=float(i)))
        entries = s.entries_for("t")
        assert [e.seq for e in entries] == [1, 2, 3]

    def test_clear(self) -> None:
        s = InMemoryStore()
        s.save(LedgerEntry(turn_id="t", seq=1, phase="yielding"))
        s.clear()
        assert s.entries_for("t") == []
        assert s.next_seq("t") == 1


class TestSQLiteStore:
    def test_roundtrip(self, tmp_path) -> None:
        db = tmp_path / "ledger.db"
        s = SQLiteStore(str(db))
        e = LedgerEntry(
            turn_id="t1",
            seq=1,
            phase="yielding",
            cursor=TurnCursor(token_index=7, step_index=1, sub_step="llm:streaming"),
            payload={"emitted_so_far": ["a", "b"], "adapter": "text_stream"},
            yielded_at=12345.6,
        )
        s.save(e)
        latest = s.latest_for("t1")
        assert latest.cursor.token_index == 7
        assert latest.cursor.sub_step == "llm:streaming"
        assert latest.payload["emitted_so_far"] == ["a", "b"]
        assert latest.yielded_at == pytest.approx(12345.6)

    def test_persistence_across_connections(self, tmp_path) -> None:
        db = tmp_path / "ledger.db"
        s1 = SQLiteStore(str(db))
        s1.save(LedgerEntry(turn_id="t", seq=1, phase="yielding", yielded_at=1.0))
        s1.close()
        s2 = SQLiteStore(str(db))
        assert s2.latest_for("t").seq == 1
        assert s2.next_seq("t") == 2

    def test_latest_yielding_skips_resumed(self, tmp_path) -> None:
        s = SQLiteStore(str(tmp_path / "ledger.db"))
        s.save(LedgerEntry(turn_id="t", seq=1, phase="yielding", yielded_at=1.0, resumed_at=2.0))
        assert s.latest_yielding_for("t") is None
        s.save(LedgerEntry(turn_id="t", seq=2, phase="yielding", yielded_at=3.0, resumed_at=None))
        latest = s.latest_yielding_for("t")
        assert latest is not None and latest.seq == 2


# ------------------------------------------------------ save/yield/resume core


class TestLedgerHandshake:
    def test_inmemory_ledger_is_a_turnledger(self) -> None:
        ledger = InMemoryLedger()
        # runtime_checkable Protocol: any object with the three methods matches.
        assert isinstance(ledger, TurnLedger)

    def test_suspend_returns_yielding_entry(self) -> None:
        ledger = InMemoryLedger()
        entry = ledger.suspend("t1", cursor=TurnCursor(token_index=3))
        assert entry.turn_id == "t1"
        assert entry.seq == 1
        assert entry.phase == "yielding"
        assert entry.yielded_at is not None
        assert entry.resumed_at is None
        assert entry.cursor.token_index == 3

    def test_resume_copies_cursor_and_payload(self) -> None:
        ledger = InMemoryLedger()
        ledger.suspend(
            "t1",
            cursor=TurnCursor(token_index=12, step_index=1, sub_step="tool_call:args"),
            payload={"partial_args": '{"query": "sel'},
        )
        resumed = ledger.resume("t1")
        assert resumed is not None
        assert resumed.phase == "resuming"
        assert resumed.cursor.token_index == 12
        assert resumed.cursor.sub_step == "tool_call:args"
        assert resumed.payload["partial_args"] == '{"query": "sel'
        assert resumed.resumed_at is not None

    def test_resume_returns_none_when_nothing_suspended(self) -> None:
        ledger = InMemoryLedger()
        assert ledger.resume("nope") is None

    def test_is_resumable(self) -> None:
        ledger = InMemoryLedger()
        assert not ledger.is_resumable("t1")
        ledger.suspend("t1", cursor=TurnCursor(token_index=0))
        assert ledger.is_resumable("t1")
        ledger.resume("t1")
        assert not ledger.is_resumable("t1")

    def test_suspend_uses_last_reported_cursor_when_none_given(self) -> None:
        ledger = InMemoryLedger()
        # The adapter streams its cursor forward via report(); suspend with
        # no args should capture the freshest position.
        ledger.report("t1", cursor=TurnCursor(token_index=42), payload={"emitted": ["x"]})
        entry = ledger.suspend("t1")
        assert entry.cursor.token_index == 42
        assert entry.payload["emitted"] == ["x"]

    def test_append_only_history_records_suspend_and_resume(self) -> None:
        ledger = InMemoryLedger()
        ledger.suspend("t1", cursor=TurnCursor(token_index=5), payload={"k": "v"})
        ledger.resume("t1")
        history = ledger.history("t1")
        assert len(history) == 2
        assert [e.phase for e in history] == ["yielding", "resuming"]
        assert [e.seq for e in history] == [1, 2]

    def test_multi_turn_isolation(self) -> None:
        ledger = InMemoryLedger()
        ledger.suspend("t1", cursor=TurnCursor(token_index=10))
        ledger.suspend("t2", cursor=TurnCursor(token_index=20))
        assert ledger.resume("t1").cursor.token_index == 10
        assert ledger.resume("t2").cursor.token_index == 20
        assert ledger.history("t1")[0].seq == 1
        assert ledger.history("t2")[0].seq == 1  # seq is per-turn

    def test_multiple_suspends_then_resumes_in_order(self) -> None:
        ledger = InMemoryLedger()
        ledger.suspend("t1", cursor=TurnCursor(token_index=3))
        ledger.resume("t1")
        ledger.suspend("t1", cursor=TurnCursor(token_index=8))
        ledger.resume("t1")
        history = ledger.history("t1")
        assert [e.seq for e in history] == [1, 2, 3, 4]
        assert [e.phase for e in history] == ["yielding", "resuming", "yielding", "resuming"]

    def test_sqlite_ledger_handshake_matches_inmemory(self, tmp_path) -> None:
        # Same handshake, durable store. The Store ABC guarantees parity.
        ledger = SQLiteLedger(str(tmp_path / "ledger.db"))
        ledger.suspend("t1", cursor=TurnCursor(token_index=9), payload={"k": "v"})
        resumed = ledger.resume("t1")
        assert resumed is not None
        assert resumed.cursor.token_index == 9
        assert resumed.payload == {"k": "v"}
        assert len(ledger.history("t1")) == 2


# ---------------------------------------------------- m1 text-stream adapter


class TestTextStreamAgent:
    TOKENS = ["The", " ", "quick", " ", "brown", " ", "fox", " ", "jumps"]

    def test_no_interrupt_emits_all_tokens(self) -> None:
        ledger = InMemoryLedger()
        agent = TextStreamAgent(self.TOKENS, ledger, turn_id="t1")
        emitted = agent.run()
        assert emitted == self.TOKENS
        # No suspend/resume entries since no interrupt.
        assert ledger.history("t1") == []

    def test_ledger_mode_resumes_from_interrupted_token(self) -> None:
        ledger = InMemoryLedger()
        agent = TextStreamAgent(self.TOKENS, ledger, turn_id="t1")
        interrupt_at = self.TOKENS.index("brown")  # token index 4
        captured_user: list[str] = []

        def on_yield(_entry: LedgerEntry) -> str:
            captured_user.append("and the dog?")
            return "and the dog?"

        emitted = agent.run(should_interrupt=_oneshot(interrupt_at), on_yield=on_yield)
        # Ledger mode: the prefix is emitted once, then the agent resumes from
        # the interrupted token — full utterance, no re-emission of the prefix.
        assert emitted == self.TOKENS
        assert captured_user == ["and the dog?"]
        # Ledger recorded a suspend + a resume.
        history = ledger.history("t1")
        assert [e.phase for e in history] == ["yielding", "resuming"]
        assert history[0].cursor.token_index == interrupt_at

    def test_restart_mode_re_emits_prefix(self) -> None:
        ledger = InMemoryLedger()
        agent = TextStreamAgent(self.TOKENS, ledger, turn_id="t1")
        interrupt_at = self.TOKENS.index("brown")

        emitted = agent.run(
            should_interrupt=_oneshot(interrupt_at),
            on_yield=lambda _e: "and the dog?",
            restart_on_interrupt=True,
        )
        # Restart mode: the prefix up to the interrupt is emitted, then the
        # whole utterance is re-emitted from the top — the visible gap.
        expected = self.TOKENS[:interrupt_at] + self.TOKENS
        assert emitted == expected
        # The suspend still hit the audit log; resume was never called.
        history = ledger.history("t1")
        assert [e.phase for e in history] == ["yielding"]

    def test_report_streams_cursor_into_ledger(self) -> None:
        ledger = InMemoryLedger()
        agent = TextStreamAgent(self.TOKENS, ledger, turn_id="t1")
        agent.run(should_interrupt=_oneshot(3), on_yield=lambda _e: "stop")
        # After suspend, the agent should have reported cursors for tokens
        # 0..3 (the predicate fires AT token 3, before emitting it).
        # The suspend entry's cursor must point at the interrupted token.
        suspended = [e for e in ledger.history("t1") if e.phase == "yielding"][0]
        assert suspended.cursor.token_index == 3


class TestTextBargeInSimulator:
    def test_contrast_ledger_shorter_than_restart(self) -> None:
        tokens = list("The quick brown fox jumps over the lazy dog")
        sim = TextBargeInSimulator(
            tokens,
            interrupt_at=tokens.index("b"),
            user_utterance="add something",
        )
        result = sim.contrast()
        ledger_len = len(result["ledger_mode"]["emitted"])
        restart_len = len(result["restart_mode"]["emitted"])
        # Ledger mode emits each token once; restart mode re-emits the prefix.
        assert ledger_len == len(tokens)
        assert restart_len > ledger_len

    def test_ledger_history_has_suspend_and_resume(self) -> None:
        tokens = list("abcdefghij")
        sim = TextBargeInSimulator(tokens, interrupt_at=4, user_utterance="x")
        result = sim.run_ledger_mode()
        phases = [e.phase for e in result["history"]]
        assert phases == ["yielding", "resuming"]

    def test_user_utterance_captured(self) -> None:
        tokens = list("abcdef")
        sim = TextBargeInSimulator(tokens, interrupt_at=2, user_utterance="wait")
        result = sim.run_ledger_mode()
        assert result["user_utterances"] == ["wait"]


# ----------------------------------------------------- m2 LiveKit adapter API


class TestLiveKitAdapterAPI:
    """Verify the adapter wires the REAL livekit-agents surface.

    Two layers of guard:
      1. If livekit-agents is NOT installed, importing the adapter must not
         crash and instantiating the class must raise a clear ImportError.
      2. If it IS installed, the adapter must reference the real names
         (Agent.allow_interruptions, Agent.interruption_detection,
         SpeechHandle.allow_interruptions, RunContext.disallow_interruptions,
         Agent.on_user_turn_completed, OverlappingSpeechEvent) — verified by
         attribute presence, not by a live room.
    """

    def test_module_imports_without_livekit(self) -> None:
        # livekit may or may not be installed in the test env; either way the
        # module must import.
        mod = importlib.import_module("turnledger.adapters.livekit")
        assert hasattr(mod, "LiveKitLedgerAgent")
        assert hasattr(mod, "attach_ledger")

    def test_stub_raises_clear_importerror_when_livekit_absent(self) -> None:
        mod = importlib.import_module("turnledger.adapters.livekit")
        if mod._LIVEKIT_AVAILABLE:
            pytest.skip("livekit-agents installed; stub path not exercised")
        with pytest.raises(ImportError, match="livekit-agents"):
            mod.LiveKitLedgerAgent(instructions="x", ledger=InMemoryLedger())  # type: ignore[call-arg]

    def test_adapter_references_real_api_names(self) -> None:
        mod = importlib.import_module("turnledger.adapters.livekit")
        source_path = mod.__file__
        assert source_path is not None
        source = open(source_path, encoding="utf-8").read()
        # The load-bearing real API names must appear in the adapter source.
        required = [
            "allow_interruptions",
            "interruption_detection",
            "TurnHandlingOptions",
            "InterruptionOptions",
            "SpeechHandle",
            "RunContext",
            "disallow_interruptions",
            "on_user_turn_completed",
            "OverlappingSpeechEvent",
            "on_enter",
            "overlapping_speech",
        ]
        missing = [name for name in required if name not in source]
        assert not missing, f"adapter missing real API names: {missing}"
        # The non-existent hook must NOT be referenced.
        assert "handle_interrupt" not in source, (
            "adapter must NOT reference the non-existent Agent.handle_interrupt"
        )

    def test_overlapping_speech_event_shape(self) -> None:
        # The OverlappingSpeechEvent pydantic model has the fields we depend on.
        # We build one synthetically (no livekit room needed) so the test
        # documents the contract: is_interruption, agent_ended, detected_at.
        mod = importlib.import_module("turnledger.adapters.livekit")
        if not mod._LIVEKIT_AVAILABLE:
            pytest.skip("livekit-agents not installed")
        ev_cls = mod._LKOverlappingSpeechEvent
        ev = ev_cls(is_interruption=True, agent_ended=False, detected_at=1.0)
        assert ev.type == "overlapping_speech"
        assert ev.is_interruption is True

    def test_suspend_called_on_overlapping_speech_through_fake_session(self) -> None:
        # Drive the adapter's suspend path with a fake session + fake event,
        # without a livekit room. This is the m2 wiring test: the barge-in
        # event must reach ledger.suspend().
        mod = importlib.import_module("turnledger.adapters.livekit")
        if not mod._LIVEKIT_AVAILABLE:
            pytest.skip("livekit-agents not installed")

        ledger = InMemoryLedger()
        # Build an event the way the pydantic model expects.
        ev = mod._LKOverlappingSpeechEvent(is_interruption=True, agent_ended=False)
        fake_session = _FakeSession()
        handler = mod.attach_ledger(fake_session, ledger, turn_id="t-lk")
        # Dispatch the event as the session would.
        handler(ev)
        assert ledger.is_resumable("t-lk")
        suspended = ledger.latest("t-lk")
        assert suspended.cursor.sub_step == "voice:playout"
        assert suspended.payload["adapter"] == "livekit"

    def test_non_interrupting_overlap_does_not_suspend(self) -> None:
        mod = importlib.import_module("turnledger.adapters.livekit")
        if not mod._LIVEKIT_AVAILABLE:
            pytest.skip("livekit-agents not installed")
        ledger = InMemoryLedger()
        fake_session = _FakeSession()
        handler = mod.attach_ledger(fake_session, ledger, turn_id="t-lk")
        # A backchannel overlap (is_interruption=False) must not suspend.
        ev = mod._LKOverlappingSpeechEvent(is_interruption=False)
        handler(ev)
        assert not ledger.is_resumable("t-lk")


class _FakeSession:
    """Minimal stand-in for an AgentSession's event-subscription surface.

    The real ``AgentSession`` is an ``rtc.EventEmitter`` with ``.on(event, cb)``.
    For the wiring test we only need to capture the registered callback so we
    can dispatch a synthetic event — we never start a room.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}

    def on(self, event: str, callback: Any) -> Any:  # noqa: D401
        self._handlers[event] = callback
        return callback

    def emit(self, event: str, arg: Any) -> None:
        self._handlers[event](arg)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
