# What SeeQ costs to run: $0/hour. Really.

**SeeQ needs no AI, no API key, no account, and no internet (beyond NTP time sync) to
operate.** Every part of the QSO loop is codified logic, not a language model:

| Function        | What does it                                      | Tokens |
|-----------------|---------------------------------------------------|--------|
| Decode          | `jt9` (WSJT-X's C/Fortran decoder)                | 0      |
| Choose a target | `qso.py` ranking (SNR floor, dupes, CQ filters)   | 0      |
| Sequence a QSO  | `qso.py` state machine (ZL2IFB etiquette rules)   | 0      |
| Generate TX     | `ft8code` + `tools/ft8synth.py` (GFSK synthesis)  | 0      |
| Key the rig     | `rigctl` + independent watchdog                   | 0      |
| Display         | `dashboard.py` (stdlib Python, offline SVG map)   | 0      |
| Log             | ADIF file writes                                  | 0      |

This is by design, not accident. An LLM has no place inside a 15-second hard-realtime
FT8 slot: it is too slow, too expensive, and less correct than a state machine that
encodes the operating rules exactly. Claude **built** this program; Claude does not
**run** it. Clone, edit `station.conf`, run `seeq chase 5` — total marginal cost: your
electricity.

## So where did the money go?

Development. Measured from this project's actual session logs (July 2026): building
SeeQ — protocol study, the chaser engine, the map dashboard, debugging RFI and USB
gremlins live at the bench — burned an API-equivalent **~$0.14/minute (~$8/hour)** on a
frontier model, ~97% of it prompt-cache reads from working in one giant multi-day
conversation. A Claude subscription capped this well below raw API price.

## If you want to tinker with the code, cheaply

Options in ascending order of cost:

1. **No AI ($0).** It's ~2,500 lines of commented Python/bash with a test suite
   (`tools/test_sequencer.py`). Hams have hacked far worse. PRs welcome.
2. **Local model ($0 + a GPU you may already own).** [Ollama](https://ollama.com) with a
   coding model (e.g. `qwen2.5-coder:14b` on 12 GB VRAM) handles small, well-scoped
   edits: "add a config option", "change a widget default". Expect to review its work
   against the test suite. Free, private, offline — very ham.
3. **Cheap hosted model (cents/hour).** Claude Haiku 4.5 is $1/M input, $5/M output —
   roughly 15× cheaper than the frontier tier that built this. In Claude Code:
   `/model haiku`, or give subagents `model: haiku`. Fine for focused fixes.
4. **Mid-tier for features ($3/$15 per M).** Claude Sonnet for real feature work; a
   $20/month Claude Pro plan is more than enough for occasional-evening tinkering.

**The single biggest lever isn't the model — it's session hygiene.** Our bill was 97%
cache-reads because one conversation carried days of context. For maintenance work:
start a fresh session per task, keep sessions short, let the repo's docs (README,
MISSION.md, `agents/PREPROMPT.md`) carry the context instead of the chat history.
A scoped one-hour Haiku session costs cents; a marathon frontier session costs dollars
per hour.

## Rules this repo commits to

- The runtime will never require an AI service, an account, or telemetry.
- Any future AI-assisted feature (e.g., auto-summarizing your log) must be optional,
  off by default, and degrade gracefully to $0 operation.
- The safety chain (watchdog, frequency read-back, attended operation) stays codified —
  it must never depend on a model's judgment or availability.
