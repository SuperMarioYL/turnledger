# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-08-11

### Added
- **Core primitive (m1).** `TurnCursor` / `LedgerEntry` / `Ledger` with the
  `save` / `yield` / `resume-continue` handshake, an append-only audit log,
  and a framework-agnostic `TurnLedger` Protocol + pluggable `Store` ABC.
- **Stores.** `InMemoryStore` (default, in-process) and `SQLiteStore`
  (durable, queryable audit log). Convenience classes `InMemoryLedger` /
  `SQLiteLedger`.
- **Text-streaming adapter (m1).** `turnledger.adapters.text_stream` — a
  streaming-text agent + scripted barge-in simulator that produces the
  resume-continue vs drop-and-restart contrast.
- **LiveKit voice adapter (m2 — ship target).**
  `turnledger.adapters.livekit.LiveKitLedgerAgent` binds the ledger to
  LiveKit's real interrupt surface: `OverlappingSpeechEvent` →
  `ledger.suspend()`, `Agent.on_user_turn_completed` → `ledger.resume()`,
  on an `Agent` configured with `allow_interruptions=True` + `interruption_detection`
  (via `TurnHandlingOptions`), with per-speech override via
  `SpeechHandle.allow_interruptions` and per-function-call override via
  `RunContext.disallow_interruptions()`.
- **CLI.** `turnledger demo` — interactive text-streaming barge-in contrast
  (the 30-second pitch). `turnledger info` prints version + API surface.
- **Example.** `examples/text_barge_in_demo.py` — runnable, headless contrast.
- **Tests.** `tests/test_ledger.py` covering the core handshake, store
  parity, text adapter contrast, and LiveKit adapter API-name guards.
- **Repo shape.** Animated dark/light hero + atlas SVGs, `docs/demo.tape`,
  `ci.yml` + `release.yml` + `demo.yml` workflows, `VERSION`, `LICENSE`
  (MIT), `CHANGELOG.md`, bilingual `README.md` (zh, primary) +
  `README.en.md`.

### Notes
- m3 (cross-framework save/yield/resume spec + pipecat/dify adapter stubs)
  is roadmap only in v0.1 — not built.
- TurnLedger Cloud hosted backend is signaled in the README roadmap; the
  OSS library stays free.
