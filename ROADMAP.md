# SeeQ — ROADMAP (program level)

**Repo:** [`d4rkd0s/seeq`](https://github.com/d4rkd0s/seeq)
**Last updated:** 2026-08-05

> Two roadmaps live in this repo, deliberately:
>
> - **This file** — the *program* roadmap. The workstreams in flight and how they're sequenced.
> - **[docs/ROADMAP.md](docs/ROADMAP.md)** — the *engineering* roadmap for the station
>   software (phases, per-task model tier, acceptance criteria). Still the source of truth
>   for station-code work.
>
> Current state: [STATUS.md](STATUS.md). Working rules: [CLAUDE.md](CLAUDE.md).

---

## Workstreams

| # | Workstream | State | Next |
|---|-----------|-------|------|
| A | FT8 station engine | Shipped, maintained | Bug fixes and dashboard refinement |
| B | JS8 native mode | In development | Decoder core, then ungate the UI |
| C | Operator-loop codification (docs/ROADMAP Phase 4) | Partially done | `seeq report`, log sync, dashboard alerts |
| D | Project website | Not started | Tracked outside this repo |

---

## A — FT8 station engine

Shipped and working: decode, chase, logging, and the live dashboard (waterfall, offline
world map, decode table, QSO log, next-call ranking), on any Hamlib-controllable rig with a
USB audio interface. The TX safety chain is frozen code — frequency read-back before every
key-up, an independent pre-armed unkey watchdog, and attended-only gates. Changes there
need the full test suite plus control-operator review. Ongoing work is refinement, not
new architecture.

## B — JS8 native mode

Pivoting from driving JS8Call to a native implementation. The encoder core has landed —
wire spec, CRC-12, LDPC, frame construction — and the mode is gated as *In Development* in
the dashboard until the round trip is real. Next is the decode path, then the same TX
safety chain applied to JS8 before the gate comes off. The vendored JS8Call-improved
AppImage stays pinned at v3.0.2 in the meantime.

## C — Codify the operator loop

[docs/ROADMAP.md](docs/ROADMAP.md) Phase 4: replace the remaining supervision tasks with
programs — `seeq report` for session summaries, idempotent log sync, and dashboard alerts
for QSO completion, chase end, watchdog fire, and decode silence. Each one removes a reason
to have an AI watching the station.

## D — Project website

A public-facing website for SeeQ is planned but **not started**. It is tracked in a
separate repo, not here, and is gated behind a scoping interview with the project owner —
audience, message, scope, hosting, and how prominently the safety model is presented. No
pages, stack, or copy exist yet, by design: a site that speaks for a licensed amateur
station needs the control operator's direction before it is built, not after.

This repo carries no website code. When the site ships, this row gets a link and
[STATUS.md](STATUS.md) gets updated.

---

## Sequencing

```
A (shipped) ──┐
B (JS8)    ───┼──► ongoing station work, none of it blocking the others
C (Phase 4)───┘

D (website) ──► gated on owner scoping interview; tracked in its own repo
```

---

### Related documents

[STATUS.md](STATUS.md) · [CLAUDE.md](CLAUDE.md) · [docs/ROADMAP.md](docs/ROADMAP.md) · [MISSION.md](MISSION.md) · [README.md](README.md)
