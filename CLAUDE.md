# COTA — Claude on the Air: FT8 station

**Project:** Command-line FT8 chaser + live dashboard for Xiegu G90 + DE-19 interface. Runtime: $0 (no AI, no API keys). Tinkering: cents/hour with Haiku or free with local models.

## Project structure

```
bin/          coa (entrypoint), rx-loop.sh, dashboard.py, qso.py, world_map.py
tools/        ft8synth.py, test_sequencer.py, jt9_wisdom.dat (reference)
agents/       PREPROMPT.md (safety), role-specific pre-prompts
data/         slot.wav, decodes/YYYY-MM-DD/HH.jsonl (rotated decode log), status.json
docs/         ROADMAP.md (Phase 2 now), COST.md, LOCAL-MODELS.md (Phase 3)
station.conf  Config (never commit; copy from station.conf.example, edit for your rig)
```

## Safety (ABSOLUTE — violation = mission failure)

From `agents/PREPROMPT.md` — these are codified in the watchdog and frequency read-back chain:

1. **Never key PTT / transmit autonomously.** TX out of scope unless Logan's explicit go with announced duration.
2. **Frequency read-back before every key-up** (`rigctl f` must match configured dial exactly).
3. **Independent pre-armed unkey watchdog** (default 14 s, before every frame; fires even if main process dies).
4. **Attended semi-automation only** — Logan stays at the radio when chasing.
5. **No hold on CAT serial port while WSJT-X runs.** Use PulseAudio for audio capture only.

**TX safety chain is frozen code** — no cheap-model or local-model session may modify watchdog, frequency verification, or attended gates without full test suite + control-operator review.

## Test commands

```bash
python3 tools/test_sequencer.py         # Unit tests for QSO state machine
python3 tools/test_qrz.py               # Unit tests for ADIF/QRZ-API/logbook merge
python3 tools/test_pipeline.py          # Unit tests for station.conf, decode storage, report, GFSK synth
python3 -m py_compile bin/*.py tools/*.py
bash -n bin/*.sh bin/coa                # Bash syntax check
# or just: make test — runs all of the above (also what CI runs)
```

**Before commit:** run `make test` (or the three suites + syntax checks individually).

## Releasing

**Every push of new code gets a version tag + GitHub Release** — don't let commits pile up
unreleased. Current: **v1.1.0** (v1.0.0 was the first tagged release).

1. Test gate first: `python3 -m py_compile bin/*.py tools/*.py`, `bash -n bin/*.sh bin/coa`,
   `python3 tools/test_sequencer.py` (must stay green).
2. **Secrets/callsign sweep before anything touches origin** — this repo once had the operator's
   real callsign, grid locator, and a live `station.conf` exposed on GitHub for about a week
   before it was caught and scrubbed (2026-07-11 remediation: squashed history, force-pushed via
   SSH, verified server-side via the GitHub API). Never repeat that: `git diff` must not contain
   the operator's real callsign, grid square, personal email, or home directory path — check
   against the actual values in your local (gitignored) `station.conf`, never hardcode any of
   them into this file or any other tracked doc, even as a "here's what to grep for" example.
3. Bump version by semver: patch for fixes, **minor for new features** (the common case here —
   most sessions add dashboard/UI capability), major only for a breaking change to the TX safety
   contract or config format.
4. `git add <specific files>` (never `-A`/`.`) → commit → `git tag -a vX.Y.Z -m "..."` →
   `git push origin master` → `git push origin vX.Y.Z`.
5. `gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."` — GitHub auto-attaches the zip/tar.gz
   source archives to every tag by default, nothing extra to configure for that.
6. Verify server-side, not just locally — pull the pushed tag's tree via the GitHub API and grep
   raw file contents for the callsign/grid pattern above. Trust but verify what's actually public.

## Operating

```bash
cp station.conf.example station.conf    # Edit EVERY value: callsign, grid, rig, audio device
bin/coa start                           # RX only, no TX — preflight + dashboard at :8074
bin/coa chase 5                         # Answer CQs, log 5 QSOs — stay at radio!
bin/coa stop                            # Force PTT release + shutdown
```

## References

- **[README.md](README.md)** — quick start, architecture, on-air etiquette
- **[MISSION.md](MISSION.md)** — original project goals and status
- **[docs/ROADMAP.md](docs/ROADMAP.md)** — Phase 1–5 tasks, model tiers, acceptance criteria
- **[docs/COST.md](docs/COST.md)** — runtime cost ($0), dev cost breakdown, tinkering options
- **[agents/PREPROMPT.md](agents/PREPROMPT.md)** — every agent reads this first: rig facts, safety chain, skill pointers

## For agents

- Use [.claude/agents/](/.claude/agents/) pre-prompts (docs-editor, ui-tweaker, engine-dev) — they embed safety rules and model pins
- Read PREPROMPT.md + relevant skill file (rig-control, de19-interface, wsjtx-ft8, antenna-atu) before any work
- Keep sessions short and scoped (one task per session)
- Never edit TX safety paths without full tests + Logan's review
