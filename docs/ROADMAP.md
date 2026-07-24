# ROADMAP — implementing the $0 cost model

Goal (Logan, 2026-07-05): a ham with no AI subscription gets on the air with SeeQ in
under 30 minutes; a ham who wants to hack on it does so for cents (hosted Haiku) or $0
(local model / no AI). Development of this roadmap itself must follow its own rules:
scoped tasks, cheap models, fresh sessions, tests as the gate.

Legend: each phase lists tasks, the model tier that should build it
(**H**=Haiku-class cheap, **S**=Sonnet-class mid, **0**=no AI needed), and acceptance.

---

## Phase 1 — Turnkey station: zero AI to get on air

The engine is already $0 at runtime; what still costs is *knowledge* — ours is in chat
history and Logan's head. Codify it.

| # | Task | Model | Notes |
|---|------|-------|-------|
| 1.1 | `seeq setup` interactive wizard | S | Detect audio cards (`pactl list short`), serial ports (`/dev/serial/by-id/`), rig via `rigctl -l` picker; write `station.conf`; verify with a CAT frequency read + RX level meter. |
| 1.2 | `seeq selftest` | H | Decode a bundled 15 s reference WAV through the jt9 chain — proves install without a radio or antenna. |
| 1.3 | `seeq doctor` | H | Preflight diagnostics with fix-it hints: NTP sync, mixer levels, CAT reachable, audio flowing, disk space. Exit nonzero on hard failures. |
| 1.4 | Install docs per distro | H | Debian/Ubuntu (apt), Fedora (dnf), Raspberry Pi OS notes (a Pi 4 runs this fine — the natural $0 shack computer). |

**Acceptance:** fresh Linux install → first decode in ≤30 min following README only.

## Phase 2 — Repo carries the context: cheap AI tinkering kit

Our measured bill was 97% cache-reads because chat history carried the project. Move
that context into files any fresh session reads in seconds.

| # | Task | Model | Notes |
|---|------|-------|-------|
| 2.1 | `CLAUDE.md` at repo root | H | Project map, safety absolutes (from agents/PREPROMPT.md), test commands, "never touch TX safety paths without tests" rule. |
| 2.2 | `.claude/agents/` roles with cheap model pins | H | docs-editor + ui-tweaker pinned Haiku; engine work pinned Sonnet; each embeds the safety preprompt. |
| 2.3 | `CONTRIBUTING.md` session-hygiene playbook | H | One task per session; run `tools/test_sequencer.py` before commit; keep sessions short; what belongs in an issue vs a session. |

**Acceptance:** a scoped fix (e.g., add a config option) completes in a fresh
Haiku session for under $0.25.

## Phase 3 — Local-model path: $0 tinkering

| # | Task | Model | Notes |
|---|------|-------|-------|
| 3.1 | `docs/LOCAL-MODELS.md` | H | Ollama install, model picks by VRAM (7B/14B/32B coder models), how to feed CLAUDE.md as system context, hard rule: local models may not edit watchdog/PTT/frequency-verification code. |
| 3.2 | `make test` / single test entrypoint | 0 | One command gating all contributions (sequencer suite + py_compile + bash -n); wire into CI. |
| 3.3 | GitHub Actions CI | H | Free tier: run the test gate on every push/PR. Protects against low-quality model patches. |

**Acceptance:** a documented local-model session produces a merged, test-passing patch.

## Phase 4 — Codify the operator loop: remove AI from OPERATIONS

The only remaining live Claude usage is supervision: status summaries, monitors, log
uploads. Replace each with a program.

| # | Task | Model | Notes |
|---|------|-------|-------|
| 4.1 | `seeq report` | H | The compact session report, codified: QSOs today/total, attempts, per-band breakdown, ADIF day summary — printed at chase end and on demand. |
| 4.2 | `bin/log-sync.sh` (QRZ first, LoTW later) | S | Idempotent: track last-synced byte offset of the ADIF, push new records via QRZ Logbook API (needs owner's XML subscription key in `~/.config/cota/qrz.key`, chmod 600, never in repo). Cron-able. |
| 4.3 | Dashboard alerts | H | Browser notifications for: QSO completed, chase ended, watchdog fired, decode silence >3 min (band died / audio broke). Replaces AI monitors. |
| 4.4 | `seeq chase` end-of-run summary → `data/session-report.txt` | H | So the operator never needs to ask anyone "how did it go?" |

**Acceptance:** a full operating evening — setup, chase, QSL upload, review — with no
AI session opened at any point.

## Phase 5 — Community (stretch)

Issue templates (bug/feature/RFI-report), tagged v1.0 release once Phase 1 lands,
dashboard screenshot + demo GIF in README, announce (QRZ forums / r/amateurradio) —
announcement text is Logan's call.

---

## Execution notes

- **Order:** 2.1→2.2 first (they make everything after cheaper), then Phase 1, 4, 3, 5.
- **Estimated build cost using this plan's own rules:** Phases 1–4 ≈ 10–14 scoped
  sessions, Haiku for ~70% of tasks ≈ **$5–15 total** — vs the ~$466 API-equivalent the
  frontier-model marathon that *created* SeeQ measured (see [COST.md](COST.md)).
- **Safety invariant for every task:** the TX safety chain (watchdog, frequency
  read-back, attended-operation gates) is frozen code — no cheap-model or local-model
  session may modify it; changes there require the full test suite plus the control
  operator's explicit review.
