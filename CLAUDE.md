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

## TDD (required, repo-wide)

Every new feature and every bug/regression fix goes red → green. No exceptions for
"it's just a UI tweak" or "it's just a one-liner":

1. **New feature:** write a test that specifies the expected behavior *before* the
   implementation exists. Run it, confirm it fails (red) — a passing test at this
   point means the test isn't testing anything.
2. **Bug/regression fix:** write a test that reproduces the bug against the current
   (broken) code first. Confirm it fails (red) for the reason you think it's failing,
   not some unrelated error.
3. Implement the minimal fix/feature, rerun, confirm green.
4. Wire the new test into `Makefile`'s `test` target so CI enforces it forever, not
   just this one session.

This is a general practice, separate from (and doesn't weaken) the stricter frozen-code
rule above — TX safety code needs full-suite + control-operator review *in addition to*
its own red/green cycle.

Dashboard UI logic lives as JS text embedded in Python strings inside `bin/dashboard.py`
— not importable Python. Don't reimplement that logic in Python to test it (the copies
drift and stop catching real bugs); extract the real JS source and execute it under
Node via subprocess instead. `tools/test_dashboard_js.py` is the reference pattern:
it slices `CALL_PREFIXES`/`callCountry()` out of `dashboard.py` between two stable
source markers and runs it with `node -e`. Reuse that approach for any future
dashboard.py JS changes (Node ships preinstalled on GitHub Actions `ubuntu-latest`
runners, no extra CI setup needed).

## Test commands

```bash
python3 tools/test_sequencer.py         # Unit tests for QSO state machine
python3 tools/test_qrz.py               # Unit tests for ADIF/QRZ-API/logbook merge
python3 tools/test_pipeline.py          # Unit tests for station.conf, decode storage, report, GFSK synth
python3 tools/test_dashboard_js.py      # Unit tests for dashboard.py's embedded JS (callCountry), run via Node
python3 -m py_compile bin/*.py tools/*.py
bash -n bin/*.sh bin/coa                # Bash syntax check
# or just: make test — runs all of the above (also what CI runs)
```

**Before commit:** run `make test` (or the suites + syntax checks individually) — every suite must be green, and per the TDD section above, any test added for this commit must have been red first.

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
- **[docs/ROADMAP.md](docs/ROADMAP.md)** — Phase 1–5 tasks, model tiers, acceptance criteria (build *process*)
- **[docs/MODES-ROADMAP.md](docs/MODES-ROADMAP.md)** — FT8 → JS8 → email-over-radio mode roadmap and mode-switching architecture (build *capability*)
- **[docs/COST.md](docs/COST.md)** — runtime cost ($0), dev cost breakdown, tinkering options
- **[agents/PREPROMPT.md](agents/PREPROMPT.md)** — every agent reads this first: rig facts, safety chain, skill pointers

## For agents

- Use [.claude/agents/](/.claude/agents/) pre-prompts (docs-editor, ui-tweaker, engine-dev) — they embed safety rules and model pins
- Read PREPROMPT.md + relevant skill file (rig-control, de19-interface, wsjtx-ft8, antenna-atu) before any work
- Keep sessions short and scoped (one task per session)
- Never edit TX safety paths without full tests + Logan's review
