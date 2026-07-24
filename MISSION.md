# FT8-Claude — Claude-driven digital contacts

> This is a historical build log (dated session notes from initial development), not
> project documentation. For the current pitch, install steps, and architecture, see
> [README.md](README.md). Entries below say "COTA"/"d4rkd0s/cota" because that was the
> project's real name at the time — it was renamed to **SeeQ** on 2026-07-23
> (`github.com/d4rkd0s/cota` now redirects to `github.com/d4rkd0s/seeq`). Left as-written
> rather than rewritten, since this file is a log, not living docs.

Mission (Logan, 2026-07-03): drive FT8 contacts through Claude, with a display Logan can
watch: **waterfall + decode list + contact log + "next call"**. Start basic, grow.

## Ground rules (never violate — see ~/Radio/skills/README.md + memory `radio-tx-safety`)

1. Logan is the control operator. Claude never transmits without his explicit go.
2. Frequency read back and verified (`rigctl f`) before every key-up.
3. TX watchdog always pre-armed and independent. Tests ≤10 s. (A real FT8 frame is
   12.64 s — needs Logan's explicit sign-off on a ~14 s watchdog before Claude ever
   sends one. NOT yet granted.)
4. Semi-automation only (attended, Logan present) — no unattended robot. FCC Part 97.
5. RX-only work needs no permission — decode/display/log freely.

## Architecture (v0 = RX side + display, working)

```
G90 ──DE-19──> PulseAudio source ──parecord 12 kHz──> data/slots/slot.wav (per 15 s slot)
                                        │
                     jt9 -8 (WSJT-X CLI decoder — best sensitivity)
                     sox spectrogram → data/waterfall.png
                                        │
                     bin/rx-loop.sh → data/decodes/YYYY-MM-DD/HH.jsonl + data/status.json
                                        │
                     bin/dashboard.py (http://localhost:8074) ← Logan's browser
```

TX path (v1, needs Logan's go + watchdog sign-off): compose reply → gen_ft8/ft8sim WAV
→ verify freq → rigctl T 1 → aplay → T 0 (+ independent watchdog). Sequencer = Claude
or a small state machine; the display's "next call" column is the human-approval queue.

## Tooling facts (researched 2026-07-03, sources in the research report)

- Already installed: `jt9` (CLI FT8 decoder, needs 12 kHz 16-bit mono WAV), `ft8code`
  (message↔bits). NOT installed: `ft8sim` (this Ubuntu wsjtx 2.5.4 package lacks it).
- TX WAV generators to add later: **kgoba/ft8_lib** (`make`; gen_ft8/decode_ft8, MIT) or
  KK5JY's prebuilt wsjtx-utils.
- Turnkey alternative if homebrew stalls: **KK5JY ft8modem + ft8cat + ft8qso**
  (RtAudio + rigctld; reported working headless station). Active Python option: **PyFT8** (pip).
- jt9 one-shot loses callsign hashtables between slots → nonstandard calls may show as <...>.
- Capture via PulseAudio so WSJT-X (set to `pulse`) and this pipeline can share the card.
  WSJT-X on raw `plughw:` blocks the pipeline (device busy) — run one or switch WSJT-X to pulse.
- Slot timing: starts :00/:15/:30/:45; decode budget ~1.5–2 s after 13.5 s capture.

## Files

- `agents/PREPROMPT.md` — paste-first context for every subagent on this project
- `agents/<role>.md` — role-specific pre-prompts (rx-pipeline, display, sequencer, tx-safety)
- `bin/rx-loop.sh` — aligned capture → decode → waterfall → status, forever
- `bin/dashboard.py` — the display server (port 8074)
- `bin/start.sh` — start display + rx loop; `bin/stop.sh` — stop both
- `data/` — slot.wav, waterfall.png, decodes/YYYY-MM-DD/HH.jsonl (rotated), status.json (runtime, gitignore-class)

## Status

- 2026-07-03: v0 scaffolded — RX loop + dashboard. TX not implemented (by design, pending
  Logan's watchdog sign-off).
- 2026-07-04 00:39Z: **FIRST QSO** — VE2OPC FN45, −08/−05, 40 m, 5 W, fully sequenced by
  `bin/qso.py` (chaser) and ADIF-logged. Logan signed off: 14 s frame watchdog, ≤6 same-frame
  repeats, QSO-to-73 on answer, staged ramp **1 ✅ → 3 → 5 → 10**. Protocol validated:
  loopback (4 msg types, DT 0.0), self-decode of all TX (DT ±0.2), PSKReporter spots in
  10 states. Engine tuning: patience 5 calls/target, pileup-aware ranking (−6 dB/competitor).
- 2026-07-04 02:1xZ **night closeout (rain shutdown): 4 QSOs in the log** — VE2OPC (FN45,
  5 W), K8GIB (EM89), KY9L (EM48), K8NWN (EN72) at 10 W; last three completed in 8 minutes
  once the band opened. 14 partial attempts in attempts.jsonl. PSKReporter: heard in
  England/Ireland/Switzerland/Canary Islands at 10 W. Map GUI shipped (offline SVG world,
  RX constellation, TX arc, info bar). Etiquette engine v2 (ZL2IFB-guided: split-only
  calling, busy-hold, SNR floor, directed-CQ filter, stalled-QSO recovery, parity/freq
  tracking, breathers) committed as `db644ac` and **verified 2026-07-05**: clean tree,
  py_compile clean, tools/test_sequencer.py 26/26 pass. Takes effect on next chaser
  launch — not yet proven on air. PENDING: Logan's visual sign-off on the red
  TX arc (task #6), then 100%.
- Known cosmetic bug (fixed in etiquette v2 pass — verify): dashboard next-call showed a
  grid for 1×1 special-event calls.
- 2026-07-05 (day session, RX-only, no TX): **repo published → github.com/d4rkd0s/cota**
  ("COTA — Claude on the Air", MIT, scrubbed, history reviewed clean). Dashboard v2
  shipped (`55119bb`): 8-widget resizable/reorderable layout persisted to data/ui-layout.json,
  map auto-zoom to activity (Auto/World toggle), sticky cockpit bar (STATE/BAND/NEXT + red
  STOP+UNKEY), Actions widget (Start/Stop RX, Chase N with 2-step confirm, target pick/skip
  request files, localhost-only, actions.log audit, COA_DRYRUN test mode). 20m RX verified
  (14.074, coast-to-coast decodes, 13 Colonies stations heard). STILL PENDING: etiquette-v2
  first on-air run, red-arc + new-UI sign-off, QRZ upload of the 4 QSOs (manual import is
  free; API needs XML Logbook Data sub $35.95/yr — Logan intends to buy), delete stray
  empty GitHub repo claude-on-air-ham-ft8 (permission denied for me — Logan deletes at
  github.com/d4rkd0s/claude-on-air-ham-ft8/settings or re-asks with approval), ferrites,
  WSJT-X 2.6.1 upgrade.
- 2026-07-05 evening: **ROADMAP Phases 1–5 BUILT** (4 agents, Haiku for docs/CI, Sonnet
  for tooling): `coa setup/selftest/doctor/report/logsync`, docs/INSTALL.md, CLAUDE.md +
  CONTRIBUTING.md + .claude/agents/ model-pinned roles, Makefile test gate + GitHub
  Actions CI + issue templates, docs/LOCAL-MODELS.md, dashboard alert bell, chase
  session-report. Commits 854c928/8f0147e/3b59c65/2163076. All verified: make test
  26/26, selftest round-trips through jt9, doctor 14 OK. Remaining from ROADMAP:
  Phase 5 v1.0 tag + announcement (Logan's call), logsync live test (needs QRZ key).

## Open-source plan (Logan, 2026-07-04): `d4rkd0s/cota` — **COTA, Claude on the Air**

Local git history started 2026-07-04 (`git log` in this folder). Before the public push:
1. Extract station config (callsign, grid, dial, CAT path, audio device names, power) from
   qso.py / rx-loop.sh / parse_decodes.py / ft8-prep.sh into one `station.conf`.
2. Write repo README: what it is, the safety model (control operator REQUIRED, attended
   semi-automation only, watchdogs, freq verification), FCC Part 97 note, install steps
   (wsjtx pkg for jt9/ft8code, sox, numpy, pulseaudio), dashboard screenshot.
3. LICENSE: MIT for our code; note that jt9/ft8code are *called* from the separately
   installed GPL WSJT-X package (not bundled).
4. Strip/parameterize anything personal (sudo notes, absolute /home/logan paths).
5. Squash-review history so no secrets/logs are in commits (data/ is gitignored already).

## Handoff note (end of 2026-07-05)

**5 commits are LOCAL-ONLY, push rejected**: the gh OAuth token lacks the `workflow`
scope needed for `.github/workflows/test.yml`. First act next session:
`gh auth refresh -h github.com -s workflow` (interactive, Logan), then `git push`.
Station: dark, PTT 0, antenna state per Logan. Engine etiquette-v2 + new dashboard
still await their first on-air run (`coa start`, then `coa chase 3`, attended).
