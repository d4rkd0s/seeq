# SeeQ — STATUS

**Repo:** [`d4rkd0s/seeq`](https://github.com/d4rkd0s/seeq)
**Last updated:** 2026-08-05

> This file is the *current state* snapshot of the software. Where it's going:
> [ROADMAP.md](ROADMAP.md). How to work in this repo: [CLAUDE.md](CLAUDE.md).

---

## The one-line state

SeeQ is a working FT8 station for Linux hams with a live browser dashboard, claude-less at
runtime ($0/hour). JS8 support is **in development**, not shipped.

## What's live and working

| Area | State | Notes |
|------|-------|-------|
| FT8 decode + chase engine | ✅ Working | `bin/seeq start`, `bin/seeq chase N` |
| Live dashboard | ✅ Working | Waterfall, offline world map, decode table, QSO log, next-call ranking |
| TX safety chain | ✅ Frozen code | Frequency read-back before key-up, pre-armed unkey watchdog, attended-only gates |
| Rig support | ✅ Generic | Any Hamlib-controllable rig with a USB audio interface |
| Setup / diagnostics | ✅ Working | `seeq setup`, `seeq selftest`, `seeq doctor` |
| Test gate / CI | ✅ Working | `make test`; GitHub Actions on every push and PR |
| Mode switcher (dashboard) | ✅ Working | Fixed 2026-07-29 |
| JS8 mode | 🚧 In development | Native encoder core landed (spec, CRC-12, LDPC, frame); gated in the UI |

## Most recent work (2026-07-29)

- Prefer never-worked stations in call ranking; gate JS8 as In Development
- JS8 P1: native encoder core (spec, CRC-12, LDPC, frame)
- Documented the JS8 native-mode pivot (wire spec, phased plan, `CLAUDE.md` section)
- Fixed the inert header switch-mode button; mode registry re-read when the chooser opens
- Fixed Freq Lock retuning the radio during a manual TUNE cycle

## Repo milestones

- **2026-07-23** — Renamed COTA → SeeQ. The old name implied Claude does the transmitting,
  which was never true; the repo, the `bin/seeq` entrypoint, and every doc were updated together.
- **2026-08-05** — Baseline docs established: `STATUS.md`, `ROADMAP.md`, `CLAUDE.md`.

See the [releases](https://github.com/d4rkd0s/seeq/releases) for the shipped version history.

## Where we are on the roadmap

- **[docs/ROADMAP.md](docs/ROADMAP.md)** (engineering): Phases 1–3 are substantially
  delivered — turnkey setup, repo-carries-context, local-model path, CI test gate.
- **[ROADMAP.md](ROADMAP.md)** (program): active work is the JS8 native-mode track and
  Phase 4, codifying the operator loop.

## How to update this file

Update it whenever the answer to "what is true right now?" changes — a shipped feature, a
new blocker, a milestone. Keep it a snapshot, not a log; history belongs in git. If the
change also moves a target or adds a workstream, mirror it into [ROADMAP.md](ROADMAP.md).

Keep this file repo-centric: what the software does and where it stands. Personal,
station-specific, and operational details do not belong in a public repo.

---

### Related documents

[CLAUDE.md](CLAUDE.md) · [ROADMAP.md](ROADMAP.md) · [docs/ROADMAP.md](docs/ROADMAP.md) (engineering detail) · [MISSION.md](MISSION.md) · [README.md](README.md)
