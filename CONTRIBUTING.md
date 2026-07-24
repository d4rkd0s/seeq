# Contributing to SeeQ

**Session hygiene is the lever.** Our development cost was 97% cache-reads because one conversation carried all context. For maintenance and features, keep sessions scoped and short — let the repo's docs (README, CLAUDE.md, ROADMAP, PREPROMPT) carry the knowledge instead of your chat history. **A scoped one-hour Haiku session costs cents; a marathon frontier-model session costs dollars per hour.**

## Core rules

1. **One task per session.** Scope your work: "add a config option", "fix the waterfall scaling", "clarify ROADMAP Phase 3". Not "general QSO improvements" or "refactor everything".
2. **Fresh session per task.** Don't chain tasks in one conversation. Close the session, read the files again, start fresh. This keeps token costs low and prevents context-drift mistakes.
3. **Use the cheapest model that can do the job:**
   - **Haiku** (~$0.01/min cache-read) — docs, CLI UX, config, comments, small fixes
   - **Sonnet** (~$0.05/min) — state-machine features, integration testing, TX safety changes
   - **No AI** — merge PRs, run the test gate, troubleshoot your own installation
4. **Test before you commit.** Run `tools/test_sequencer.py` and syntax checks (see [CLAUDE.md](CLAUDE.md)) — it's your gate.
5. **TX safety chain is frozen code.** Watchdog, frequency read-back, PTT gates, attended-operation rules — these need **full test suite + Logan's explicit review** before any change lands.

## Task types and where they belong

| What | Where | Model | Session length |
|------|-------|-------|-----------------|
| **Bug report** (e.g., "dashboard crashes on port 8074") | **Issue** on GitHub | n/a | n/a |
| **Feature idea** (e.g., "show SNR trend over time") | **Issue** + discussion | n/a | n/a |
| **Implement a feature** (approved by Logan, scoped) | **New session** | H or S | <1 hour |
| **Fix a bug** (small, isolated, no TX safety) | **New session** | H | <30 min |
| **TX safety or watchdog change** | **Issue + Logan's approval first**, then session | S | Full test suite mandatory |
| **Docs/README/comment clarification** | **Session** if obvious, **issue** if architectural | H | <20 min |
| **Refactor a module** (qso.py, ft8synth.py) | **Issue + acceptance criteria first**, then session | S | Full test suite before commit |

## Before you start a session

1. **Read the absolute files:**
   - [CLAUDE.md](CLAUDE.md) — 2 min orientation
   - [agents/PREPROMPT.md](agents/PREPROMPT.md) — safety contract
   - Relevant skill file from `~/Radio/skills/` if you're touching hardware/audio
2. **State your scope out loud** (in the session opening prompt): "Add a `--verbose` flag to `seeq` that prints per-slot decodes."
3. **Check the test gate** (`python3 tools/test_sequencer.py`) to see what's already covered.

## Workflow: write code, then test

```bash
# 1. Make your change (edit bin/dashboard.py, tools/ft8synth.py, etc.)

# 2. Syntax check
python3 -m py_compile bin/*.py tools/*.py
bash -n bin/*.sh bin/seeq

# 3. Run unit tests
python3 tools/test_sequencer.py

# 4. Manual integration test if it's qso.py or state-machine work
COA_DRYRUN=1 bin/seeq start  # dry-run, no TX
# (stop with Ctrl-C)

# 5. Commit if all tests pass
git add bin/dashboard.py tools/some_module.py  # specific files, not -A
git commit -m "Your message.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"

# 6. Don't push; let Logan review locally or open a PR for discussion
```

## PR expectations

- **Tests pass:** `tools/test_sequencer.py` exit 0, syntax checks clean
- **No personal data at all** — no real callsign, grid square, or device path anywhere in a tracked file. Station identity lives only in `station.conf`, which is gitignored; examples use placeholders like `N0CALL`/`AA00`.
- **No TX safety changes** unless fully scoped in the PR title + approved by Logan
- **One focused fix per PR.** If you're tempted to fix three unrelated things, open three PRs.
- **Commit message format:** "Brief description. (Optional longer context.)"
  - If it's a TX safety or state-machine change, add: "Tested with `test_sequencer.py`; Logan review required."

## Cost tracking (optional but encouraged)

If you're using Claude Code / a Claude subscription:

```bash
# At the start of your session, note:
# - Task: "Add verbose flag to seeq"
# - Model: haiku
# - Expected time: <30 min
# - Session token estimate: ~3k (from CLAUDE.md alone, plus your edits)

# After commit, note the actual time and tokens.
# This helps the project stay under budget.
```

See [docs/COST.md](docs/COST.md) for historical costs and the why.

## Questions?

- **About the radio / hardware?** Read [agents/PREPROMPT.md](agents/PREPROMPT.md) skill pointers.
- **About TX safety?** Talk to Logan (the control operator). TX changes are not code-review rubber-stamped; they need his approval.
- **About scope?** Default to shorter sessions and smaller changes. It costs less and ships faster.
- **About the FT8 protocol?** See [README.md](README.md) "Architecture" section; `tools/ft8synth.py` is well-commented.

---

**TL;DR:** One task, fresh session, Haiku unless it's deep. Test before commit. Never edit TX safety without Logan's sign-off. Ship small, ship often.
