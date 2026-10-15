# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-10-16

### Fixed
- **Unreadable ledger dbs are a clean error.** `turnledger history --db`
  leaked raw `sqlite3.DatabaseError` / `OperationalError` /
  pydantic `ValidationError` tracebacks (exit 1) when the file existed but
  was not a readable ledger — non-SQLite bytes, a directory, or a row whose
  cursor/payload JSON is damaged. These now report
  `error: cannot read ledger database: ...` and exit 2, matching the
  missing-db contract. `turnledger demo --store sqlite` on a corrupt
  `./turnledger.db` gets the same treatment instead of a mid-demo traceback.
- **NULL-timestamp rows render instead of crashing.** A schema-valid
  `LedgerEntry` with `yielded_at` NULL — possible through the public Store
  ABC, whose `save` persists model-default rows verbatim — crashed
  `turnledger history` with `TypeError: unsupported format string passed to
  NoneType.__format__`. NULL timestamps now render as `-`, exactly like
  `resumed_at` already did; `--json` output is unchanged.
- **Negative `--token-delay` is a usage error.** `turnledger demo -d -1`
  crashed with `ValueError: sleep length must be non-negative` inside the
  streaming loop; it now raises a clean `--token-delay` usage error
  (exit 2), consistent with `--store` validation.

### Changed
- Version lockstep sweep to 0.3.0: `VERSION`, `pyproject.toml`
  `[project].version`, `turnledger.__version__`, CLI `--version`, the README
  hero lines, and `web/site.json` `meta.implementation_version`. A new
  `tests/test_version_consistency.py` pins the lockstep across every
  surface (the installed-binary `--version` check is skipped when
  `turnledger` is not on PATH).

## [0.2.0] — 2026-09-10

### Fixed
- **Demo audit crash.** `turnledger demo` crashed with
  `AttributeError: 'LedgerEntry' object has no attribute 'step_index'`
  (exit 1, raw traceback) after ANY barge-in: `_print_ledger_audit` read
  `entry.step_index`, but the step lives at `entry.cursor.step_index`. The
  README's documented `[ledger] cursor=token#… step=…` audit output is now
  actually reachable, and the demo exits 0 after a successful run.
- **Provenance-phase suspends are resumable.** `suspend(turn_id,
  phase="speaking"|"tool_call")` — a documented Protocol feature — wrote a
  suspended entry that `resume()` could never replay: both stores keyed
  resumability on the phase label `yielding` instead of the suspended state
  (`yielded_at` set, `resumed_at` unset). Resumability now follows
  `LedgerEntry.is_suspended`, on both `InMemoryStore` and `SQLiteStore`.
- **`--store` validation.** `turnledger demo --store <anything>` silently
  fell back to the in-memory store; invalid values are now a clean usage
  error (exit 2).

### Added
- **`turnledger history TURN_ID`** — read the durable SQLiteStore audit log
  from the CLI: one line per `LedgerEntry` (seq, phase, cursor, timestamps),
  `--json` for machine-readable output, `--db` to point at the ledger file.
  A missing db is a clean exit-2 error; an unknown turn prints an explicit
  no-entries line.
- **`turnledger --version`** — prints `turnledger <version>` and exits.

### Changed
- Version lockstep sweep to 0.2.0: `VERSION`, `pyproject.toml`
  `[project].version`, `turnledger.__version__`, CLI `--version`, and
  `web/site.json` `meta.implementation_version`.

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

[Unreleased]: https://github.com/SuperMarioYL/turnledger/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/SuperMarioYL/turnledger/releases/tag/v0.3.0
[0.2.0]: https://github.com/SuperMarioYL/turnledger/releases/tag/v0.2.0
[0.1.0]: https://github.com/SuperMarioYL/turnledger/releases/tag/v0.1.0
