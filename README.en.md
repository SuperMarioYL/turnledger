<div align="right"><sub><b>English</b>&nbsp;&nbsp;⇄&nbsp;&nbsp;<a href="./README.md">简体中文</a></sub></div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./assets/hero-light.svg">
  <img src="./assets/hero-light.svg" width="880" alt="TurnLedger — interruption-continuation ledger for real-time agents">
</picture>

<p align="center"><sub>The interruption-continuation ledger primitive for real-time voice agents — yield the floor on barge-in, resume continuing from the interrupted point.</sub></p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="license"></a>
  <a href="https://github.com/SuperMarioYL/turnledger/releases"><img src="https://img.shields.io/github/v/release/SuperMarioYL/turnledger?color=8985FF" alt="latest release"></a>
  <a href="https://github.com/SuperMarioYL/turnledger/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/SuperMarioYL/turnledger/ci.yml?branch=main&label=ci" alt="ci"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python" alt="python">
  <img src="https://img.shields.io/badge/agent-resume--continuity-8985FF" alt="agent anchor">
  <img src="https://img.shields.io/badge/barge--in-save%20%2F%20yield%20%2F%20resume-10A37F" alt="barge-in resume">
</p>

**When a user barges in mid-utterance, TurnLedger saves the real-time voice agent's suspended mid-step state, yields the floor, and resumes continuing from the interrupted token after the user finishes — instead of talking over the user or restarting the whole turn.**

> After pushing, set repo topics: `gh repo edit --add-topic agent --add-topic realtime --add-topic livekit --add-topic barge-in`

## Table of Contents

- [Architecture](#architecture)
- [Why this exists](#why-this-exists)
- [Install & Quickstart](#install--quickstart)
- [Usage](#usage)
- [Demo](#demo)
- [Comparison](#comparison)
- [Roadmap](#roadmap)
- [Pricing](#pricing)
- [License](#license)

<h2><img src="https://api.iconify.design/tabler:topology-star-3.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Architecture</h2>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/atlas-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./assets/atlas-light.svg">
  <img src="./assets/atlas-light.svg" width="880" alt="Architecture: Agent streaming → barge-in → TurnLedger suspend → LedgerEntry audit log → resume → continue from cursor">
</picture>

The core is the **interruption-continuation ledger**: an append-only log of `LedgerEntry` records that capture the agent's suspended mid-step state at each barge-in. The same agent can therefore *yield* the floor and *resume-continue* its in-flight turn — not abort-and-restart it.

```python
TurnCursor(token_index, step_index, sub_step)   # where in the in-flight turn we were
LedgerEntry(turn_id, seq, phase, cursor, payload, yielded_at, resumed_at)
TurnLedger.suspend(turn_id) -> LedgerEntry       # capture cursor+payload, mark yielded
TurnLedger.resume(turn_id) -> LedgerEntry | None  # replay latest, mark resumed
TurnLedger.is_resumable(turn_id) -> bool
```

<h2><img src="https://api.iconify.design/tabler:bulb.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Why this exists</h2>

Real-time voice agents have no primitive for continuation after interruption. Today's two failure modes: **talking over the user** (ignore the barge-in and plow on) or **abort-and-restart** (throw away the in-flight partial work and re-state the whole turn from the top). The latter is the default for full-duplex assistants (Alexa, Google Assistant, legacy IVR, and CN voice bots like Doubao / Xiaoai / Xiaobu) — their turn model is command→response, where "restart" is harmless because there's no partial work worth saving. Continuation only matters once a turn is **long, streaming, and stateful** — a turn model that only landed in the last cycle.

TurnLedger is the save/yield/resume-continue primitive that [affaan-m/ECC](https://github.com/affaan-m/ECC)-style agent control surfaces don't yet expose — the verb real-time Agents are missing. It defines a new verb: **save / yield / resume-continue**, structurally absent from Temporal/DBOS (step-granular crash-recovery) and from fork primitives (counterfactual divergence): the former's granularity is a seconds-long workflow step, the latter's semantics are "branch away" — not "hold one in-flight turn and continue it".

<h2><img src="https://api.iconify.design/tabler:rocket.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Install & Quickstart</h2>

```bash
pip install turnledger
turnledger demo
# press Enter mid-stream to barge in; type a line + Enter to finish — the agent resumes from the interrupted token
```

Toggle contrast mode to see the default drop-and-restart behavior side by side:

```bash
turnledger demo --contrast     # same turn: resume-continue vs restart-from-top, visible side by side
```

<details><summary>Sample output</summary>

```
TurnLedger — barge-in contrast demo
Press Enter mid-stream to barge in; type a line + Enter to finish.
================================================================================
▶ TurnLedger (resume-continue)
  So if you want to pick a plan, the Pro tier covers five concurrent agent
  ⏸ agent yielded the floor
  ⤵ user barge-in: and multi-region?
  ▶ resume from token #14
  ⋯ continuing: seats with the durable ledger and the observability API, ...
  [ledger] turn=turn-demo suspended_entries=2

--------------------------------------------------------------------------------
▶ Default (drop-and-restart)
  So if you want to pick a plan, the Pro tier covers five concurrent agent
  ⏸ agent yielded the floor
  ⤵ user barge-in: and multi-region?
  ↻ restart (drop-and-restart default)
  ⋯ from the top: So if you want to pick a plan, the Pro tier covers ...
  [ledger] turn=turn-demo suspended_entries=1
```
</details>

<h2><img src="https://api.iconify.design/tabler:terminal-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Usage</h2>

**1. In-process ledger: three lines to wire save/yield/resume**

```python
from turnledger import InMemoryLedger, TurnCursor

ledger = InMemoryLedger()
# While the agent streams, keep reporting the current cursor
ledger.report("turn-1", cursor=TurnCursor(token_index=12, sub_step="llm:streaming"))
# User barges in: suspend and yield the floor
entry = ledger.suspend("turn-1")
# User finishes: resume, continuing from the interrupted token
resumed = ledger.resume("turn-1")
assert resumed.cursor.token_index == 12
```

**2. Durable audit log (SQLiteStore)**

```python
from turnledger import SQLiteLedger

ledger = SQLiteLedger("turnledger.db")   # durable across restarts, queryable for audit
ledger.suspend("turn-1", cursor=TurnCursor(token_index=12),
               payload={"partial_args": '{"query":"sel'})
# ledger.history("turn-1")  -> [yielding, resuming, ...] the full interruption history
```

**3. LiveKit voice agent (m2, ship target)**

```python
from turnledger import InMemoryLedger
from turnledger.adapters.livekit import LiveKitLedgerAgent

class MyVoiceAgent(LiveKitLedgerAgent):
    def __init__(self):
        super().__init__(
            instructions="You are a helpful real-time voice agent.",
            ledger=InMemoryLedger(),
            interruption_detection="adaptive",   # allow_interruptions=True + ML detector
        )
    # OverlappingSpeechEvent(is_interruption=True) -> ledger.suspend()
    # on_user_turn_completed                   -> ledger.resume()
    # per-speech override:  speech_handle.allow_interruptions = False
    # per-function-call override: run_ctx.disallow_interruptions()
```

More in [`examples/text_barge_in_demo.py`](./examples/text_barge_in_demo.py) — a headless, reproducible contrast script.

<h2><img src="https://api.iconify.design/tabler:photo.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Demo</h2>

![demo](assets/demo.gif)

`docs/demo.tape` is the [vhs](https://github.com/charmbracelet/vhs) script of this 10-minute happy path; `.github/workflows/demo.yml` re-renders `assets/demo.gif` on manual dispatch.

<h2><img src="https://api.iconify.design/tabler:scale.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Comparison</h2>

TurnLedger vs [affaan-m/ECC](https://github.com/affaan-m/ECC) (en anchor repo — an agent control surface that would host save/yield/resume semantics):

| Axis | TurnLedger | affaan-m/ECC |
|---|---|---|
| save / yield / resume-continue | ✓ primitive | — control surface, not resume |
| LiveKit barge-in wiring | ✓ m2 adapter | partial (agent control) |
| Agent runtime breadth | — single primitive | ✓ broader control surface |
| Audit log | ✓ SQLiteStore | ✓ |
| Cross-framework spec | partial (m3 roadmap) | — |

ECC is broader as an agent control surface; TurnLedger is deeper on a single verb — the continuation of one interrupted turn.

<h2><img src="https://api.iconify.design/tabler:map-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Roadmap</h2>

- [x] **m1** — ledger core: `TurnCursor` / `LedgerEntry` / `InMemoryStore` + `SQLiteStore` + save/yield/resume `Protocol` + `turnledger demo` text-streaming CLI with contrast mode
- [x] **m2** — LiveKit voice adapter (ship target): `OverlappingSpeechEvent` → `ledger.suspend()`, `on_user_turn_completed` → `ledger.resume()`, on an `Agent(allow_interruptions=True, interruption_detection=...)`, with per-speech / per-function-call override
- [ ] **m3** — stabilize the save/yield/resume handshake as a cross-framework spec + pipecat / dify adapter stubs
- [ ] TurnLedger Cloud hosted backend (multi-region ledger + resume-continuity observability API)
- [ ] TS / Rust / Go SDK bindings (v0.1 is Python only)

<h2><img src="https://api.iconify.design/tabler:credit-card.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Pricing</h2>

The OSS library (`InMemoryStore` / `SQLiteStore` + all adapters) is MIT and free forever — that is how the "save / yield / resume" verb gets owned. The paid tier is **TurnLedger Cloud**: a hosted backend for production voice-agent fleets, providing multi-region ledger storage + resume-continuity observability API + compliance audit-log export, **priced per concurrent agent seat**.

| Tier | Price | What |
|---|---|---|
| Free | $0 | 10 seats, no credit card, for indie builders (the OSS library itself is unlimited) |
| Team | $499 / mo | 500 seats + audit-log export |
| Usage | $0.02 / seat·hour | Hosted ledger + observability API, billed monthly (below per-call LLM cost) |

Smallest "here's my credit card" path: `turnledger demo --voice livekit` (m2) → replay the same interruption on the ops lead's own bot → show the `LedgerEntry` audit log in the OSS `SQLiteStore` → "this is what Cloud gives you: durable + multi-region + an observability API." The Cloud backend itself is out of scope for v0.1 (see the plan's `out_of_scope`); landing page + pricing ship before m3 so the funnel exists.

<h2><img src="https://api.iconify.design/tabler:license.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> License</h2>

MIT — see [`LICENSE`](./LICENSE). File issues / PRs at [GitHub Issues](https://github.com/SuperMarioYL/turnledger/issues).

## Share this

```
TurnLedger — the save/yield/resume-continue primitive for real-time voice Agents. On barge-in it suspends mid-step, yields the floor, then resumes continuing from the interrupted token instead of restarting. github.com/SuperMarioYL/turnledger
```

<p align="center"><sub><a href="./LICENSE">MIT</a> © 2026 SuperMarioYL</sub></p>
