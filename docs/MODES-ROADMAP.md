# MODES ROADMAP — FT8 → JS8 → email-over-radio

This is the *capability* roadmap: which digital modes SeeQ drives and how the dashboard
is organized to support more than one. It's a sibling to [ROADMAP.md](ROADMAP.md), which
tracks the $0-cost build *process* instead — that one doesn't change here.

Legend matches ROADMAP.md: **H**=Haiku-class cheap, **S**=Sonnet-class mid, **0**=no AI needed.

## Ground rules for every mode (non-negotiable, inherited from CLAUDE.md)

1. **One mode owns the CAT port and audio device at a time.** The radio can only run one
   demodulator chain against one audio stream. Switching modes means stopping the current
   mode's pipeline (rx-loop + chaser, force-unkey first) before starting the next one —
   never two modes' RX/TX pipelines racing for the same hardware. This is the same rule
   the top-level `~/Radio/CLAUDE.md` already states station-wide ("never hold the CAT
   serial port while another program needs it"); modes just make it an enforced switch
   instead of an assumption.
2. **The TX safety chain applies identically in every mode.** Watchdog, frequency
   read-back, attended-operation gates — frozen code, no exceptions for "JS8 is different."
   A new mode's engine gets the same red→green TDD bar and the same control-operator
   sign-off before its first real key-up that FT8's did.
3. **Shared chrome, swapped body.** One dashboard, one URL. Header, station status
   (dial/PTT/band), and the STOP/UNKEY safety bar stay visible and functional no matter
   which mode is active — those are never mode-specific. A mode switcher swaps the main
   panel underneath (waterfall/candidate list/log layout differs per mode; email mode
   won't have a waterfall at all).
4. **A mode switch is a deliberate power-down/power-up changeover, never an instant swap.**
   Explicit instruction from Logan: switching modes should power down the current mode
   fully, wait until everything has actually stopped, run a sanity check, and only then
   bring the new mode up — with visible operational weight to it, not a slick instant
   toggle. Concretely, `seeq mode switch <name>` is a sequenced, polled changeover:
   force-unkey → request stop of the current mode's pipeline/chaser → **poll until every
   process/PTT/lockfile confirms actually stopped** (not fire-and-forget) → sanity check
   (CAT port free, PTT reads 0, no stray process holding the audio device or serial port)
   → preflight for the target mode → start the target mode's pipeline. Each stage should
   be visible to the operator (dashboard shows "shutting down FT8… / verifying clear… /
   starting JS8…"), reinforcing that this is a real operational transition, not a UI tab
   click. No mode switch may skip a stage even if it "looks" already stopped.
5. **App boot presents a mode chooser — it never silently defaults into a mode.** On
   dashboard startup, before any pipeline is running, the shared shell shows a "Welcome —
   select a mode to begin" screen instead of auto-loading whatever `station.conf`'s `MODE`
   last was. The operator picks a mode explicitly every session start; this also reinforces
   attended operation (a conscious choice each time, not an assumption carried over from
   last time) and gives the M0.3 changeover sequence above a natural first entry point
   (boot → chooser → first "switch" into the picked mode, same sequenced path as any later
   switch, no special-cased fast path for the first one).

## M0 decisions (2026-07-20, Logan's answers to `MODES-M0-QUESTIONS.md`)

- **Wrap, don't move.** `bin/qso.py`, `bin/rx-loop.sh`, `bin/parse_decodes.py` stay
  byte-for-byte untouched. `bin/modes/ft8/` is thin adapters that shell out to them.
- **FT8 stays on direct per-call `rigctl`, not a `rigctld` daemon**, for M0. Explicitly
  deferred, not rejected — Logan flagged JS8 will need its own thought on this later
  (M1's control-API research already covers this); FT8 upgrades to a shared daemon only
  if/when that becomes the right call.
- **Changeover poll timeout: 30–45 s.** Deliberate by design — Logan's framing: "so the
  HAM is deliberate in mode switching."
- **Boot chooser is dashboard-only; `seeq start` unchanged in scriptability** — no
  pipeline auto-starts on `seeq start` once M0 lands (a real behavior change from today,
  see M0's task table below). Logan flagged this one as "don't fully get it" and deferred
  to the recommendation — **revisit this specific call once M0's UI actually exists and
  is easier to react to hands-on**, don't treat it as permanently settled.
- **`seeq chase N` stays a direct, unchanged CLI fast-path** — but per Logan's explicit
  ask, it needs a **strong visual warning** (his words: "warn harder / show red/yellow
  and alert the user") since it's the one path that skips the deliberate changeover
  machinery entirely. New M0 task (M0.7) for this.

## Phase M0 — Mode abstraction (prerequisite — build before any JS8 feature work)

Today FT8 is hardcoded throughout `bin/dashboard.py`, `bin/qso.py`, `bin/parse_decodes.py`,
`bin/rx-loop.sh`. Adding JS8 as a second real mode is exactly the point where a plugin
seam earns its keep — one mode never needed it, two do.

**Split into M0a/M0b (2026-07-20, confirmed with Logan):** `dashboard.py` is one
3068-line file where the FT8 UI is ~2000 lines of HTML+JS with no existing internal
seam, and ~15 JS tests string-slice functions out of that exact `PAGE` variable by name.
Physically relocating all of that into `bin/modes/ft8/panel.py` is real, mechanical,
sizeable surgery with zero user-visible payoff until JS8 exists to be the second panel.
**M0a builds the switching machinery now** (registry, `seeq mode switch`, the changeover,
the boot chooser, the `seeq chase` warning) as purely additive code — zero lines of
`qso.py`/`rx-loop.sh`/`parse_decodes.py` touched, zero lines of `dashboard.py`'s existing
FT8 widget HTML/JS moved. **M0b (the physical `PAGE` split) is deferred** until M1
actually needs a second panel to exist — its cut points get decided by JS8's real
requirements, not speculatively now.

### M0a — the switching machinery (build now)

| # | Task | Model | Notes |
|---|------|-------|-------|
| M0a.1 | `bin/modes/<name>/` package convention: `pipeline.py` (RX/capture lifecycle — for FT8, thin wrapper reusing `dashboard.py`'s already-tested `_spawn_detached`/`_pkill`/`_proc_running` around `rx-loop.sh`), `engine.py` (TX/chase lifecycle — for FT8, thin wrapper around `_build_chase_args` + `qso.py` spawn) | S | Just FT8 for M0a; `panel.py` is M0b's concern, not built yet. |
| M0a.2 | `bin/mode_registry.py` — static registry (`{"ft8": {...}}`) + `load_mode(name)` dynamic loader; `station.conf` `MODE=ft8` field (label/default for `seeq setup`, not a runtime auto-select — see M0a.5) | S | Extension point M1 adds one entry to. |
| M0a.3 | `bin/mode_switch.py` — sequenced, polled changeover per ground rule #4: force-unkey → request stop → **poll until confirmed stopped** (30–45 s timeout, injectable clock for tests) → sanity check (CAT free, PTT=0, no stray process) → preflight target mode → start target pipeline. Writes staged progress to `data/mode-switch.json`, `data/active-mode.json` on success. Callable via `seeq mode switch <name>` (new `bin/seeq` case) or the dashboard's `/action/mode/switch`. | S | Failed sanity check always hard-aborts, never proceeds. |
| M0a.4 | Boot-time "Welcome — select a mode to begin" chooser (`#modeChooser`, reuses the existing `.modalOverlay` CSS pattern) per ground rule #5 — dashboard-only, purely additive to the current page, doesn't touch existing widget markup. `data/active-mode.json` ignored/cleared at each dashboard.py process start, so a fresh process never silently defaults. | S | |
| M0a.5 | `seeq chase N` fast-path warning — hard-to-miss ANSI red/yellow terminal banner before the chase starts, since this path skips the changeover entirely | H | Per Logan's explicit ask. Terminal-only for M0a. |

**M0a acceptance:** boot shows the mode chooser; picking FT8 runs the changeover sequence
and lands on the dashboard exactly as it looks today (nothing in `PAGE` moved); `seeq mode
switch ft8` works standalone from the CLI too; `seeq chase N` still works unchanged plus
the new warning; `make test` green throughout.

### M0b — physical panel split (deferred, not scheduled yet)

Relocate `dashboard.py`'s FT8-specific HTML/JS into `bin/modes/ft8/panel.py`; shared shell
(header, station status, STOP/UNKEY, mode-switcher nav) stays in `dashboard.py` and mounts
the active mode's panel into the body. Requires updating every `extract_*_js()` helper in
`tools/test_dashboard_js.py` that currently depends on `PAGE`'s layout. **Trigger: start
this once M1 (JS8) actually needs a second panel to exist** — let JS8's real shape decide
the cut points instead of guessing now.

## Phase M1 — JS8 mode

| # | Task | Model | Notes |
|---|------|-------|-------|
| M1.1 | ~~Audit and close out Logan's separate JS8 repo, migrate anything reusable~~ **Done 2026-07-20** | H | `d4rkd0s/js8-mastery` audited via `gh`, research folded into `~/Radio/skills/js8.md`, repo archived. |
| M1.2 | ~~JS8 decoder/control wrapper~~ **Built 2026-07-26** — `bin/modes/js8/{api,vendor,pipeline,rx_capture}.py` | S | API verified against the fork's own `docs/API.md` + `JS8_UI/Configuration.cpp`. See "What verification turned up" below. |
| M1.3 | ~~`engine.py` for JS8~~ **Built 2026-07-26** — `bin/modes/js8/{engine,watchdog}.py`. Correctly *not* a copy of `qso.py`: JS8Call-improved is itself the protocol engine, so `engine.py` is an orchestration + safety layer (confirm gate, dial read-back, pre-armed watchdog), not a state machine. | S | |
| M1.4 | ~~JS8 dashboard panel~~ **Built 2026-07-26** — six `data-mode=js8` widgets (status, actions, conversation, compose, heard, inbox) reusing FT8's widget chrome. | S | M0b resolved by `data-mode` scoping rather than the physical `PAGE` split — see below. |
| M1.5 | First on-air JS8 TX: full test suite + Logan's explicit watchdog/frequency-verification sign-off, same as FT8's original gate | — | **Still open.** Control-operator review, not a model task. Weigh the safety asymmetry below when signing off. |

### What verification turned up (M1.2)

The roadmap's instruction to verify the API against this fork rather than assume it
matches upstream paid off immediately — **the fork's own documentation is wrong about
the port.** `docs/API.md` says the API is "normally located on localhost port 2242" and
its `telnet` example connects there, but `JS8_UI/Configuration.cpp` shows 2242 is the
**UDP** default (`UDPServerPort`) and TCP defaults to **2442** (`TCPServerPort`).
Building from the prose would have connected to the wrong socket. Also confirmed from
source: both listeners ship **disabled** (`AcceptTCPRequests`/`AcceptUDPRequests` default
`false`), and settings live in `~/.config/"JS8Call - <rig-name>.ini"` under `[General]`
(`MultiSettings.cpp`'s `settings_path()` uses `ConfigLocation`, not `AppConfigLocation`).
SeeQ runs the app as `--rig-name SeeQ`, so it has its own settings file and process
signature and can never kill or reconfigure a hand-run JS8Call.

Current release is **v3.0.3**, not the v3.0.2 the earlier research recorded.

### The TX-safety asymmetry (read before M1.5 sign-off)

FT8's watchdog is genuinely independent: `qso.py` spawns a detached
`sleep N; rigctl T 0` process before every key-up, and `rigctl` drives the serial port
directly, so the backstop survives `qso.py` dying outright.

**JS8 cannot match that**, because JS8Call-improved — not SeeQ — owns the CAT port and
does the keying. SeeQ's strongest lever, `RIG.TX_HALT`, is a *request to the very process
being guarded*. What the JS8 watchdog does provide: its own detached process on its own
separate TCP connection (so a wedged command socket can't wedge the backstop), a
per-frame deadline that re-arms on each observed `RIG.PTT` push (16–32 s depending on
speed, since JS8 frames run 3.95–25.28 s versus FT8's fixed 14 s), an absolute 300 s
session cap, and a dial read-back on every key-up. What it cannot provide: any recovery
at all if JS8Call-improved hangs or crashes while keyed — no request lands, and there is
no second path to the radio. Frequency checking is also *reactive* rather than preventive
in JS8, because JS8Call's own scheduler decides when each frame starts: the first frame
of a mistuned session will have transmitted before SeeQ can abort the rest.

In that failure mode the attended operator is not one backstop among several — it is the
only one. This is documented in `bin/modes/js8/watchdog.py`, surfaced on the JS8 panel
itself, and is the reason M1.5 stays gated.

### M0b — resolved, differently than planned

M0b (physically relocating `dashboard.py`'s ~2000 lines of FT8 HTML/JS into
`bin/modes/ft8/panel.py`) was deferred until JS8 forced the question. JS8's real
requirement turned out to be *mode-scoped visibility*, not file separation: FT8 widgets
carry `data-mode=ft8`, JS8's carry `data-mode=js8`, untagged widgets are shared chrome
shown in every mode, and `applyModeVisibility()` (tested under Node like the rest of the
embedded JS) toggles them from the existing 1 s `pollModeState()` poll. That delivers the
outcome M0b existed for at a fraction of the risk, without touching the `extract_*_js()`
test helpers. The physical split remains available if `PAGE` later grows unmanageable,
but it is no longer a prerequisite for anything.

## Phase M2 — Email-over-radio (Winlink — already researched, not yet installed)

**Correction from an earlier draft of this doc:** this was written as an open "Winlink vs.
custom relay" question. It isn't — Logan already researched and decided this on
2026-07-03, independent of and before this modes roadmap existed. See
`~/Radio/skills/email-over-radio.md` (status: researched, not yet installed) for the full,
already-verified design:

- **Pat** (la5nta/pat, Winlink client, web UI on :8080) + **ardopcf** (pflarue/ardop
  ARDOP software modem — the maintained fork) + **rigctld** (shared with WSJT-X, one
  process owns `/dev/ttyUSB0`) — real internet-bridged email over 20 m, no PACTOR/VARA
  hardware needed to start.
- Concrete install commands, `pat` config JSON, session run commands, first-time Winlink
  account creation flow, and RMS gateway/dial-frequency lookup are all already written
  down there — this mode does not start from zero the way JS8 (M1) does.
- The skill file's own "Alternatives" note already draws the JS8-vs-Winlink line: JS8's
  MSG/store-and-forward (M1) is JS8-to-JS8 only; Winlink is the actual bridge to real
  internet email. Two distinct SeeQ modes, not one absorbing the other — `skills/js8.md`
  says the same thing from the other side.

What M2 actually needs, now that M0's mode abstraction exists: wrap this already-working
toolchain in a `bin/modes/email/` package. Pat's web UI already *is* the compose/inbox
interface — `panel.py` likely just needs to launch/stop Pat+ardopcf as this mode's
pipeline and embed or link out to `localhost:8080` inside the shared shell, rather than
building a new inbox UI from scratch. `engine.py` here is thin (mostly process lifecycle,
not a message state machine) compared to JS8's or FT8's.

**Content-policy flag, not just technical:** real email traffic over amateur radio has to
stay inside §97.113 (no business communications with narrow exceptions, no content that
obscures meaning) — worth Logan's explicit re-read before this mode goes live, independent
of the technical design being settled. See the FCC Part 97 notes in `~/Radio/CLAUDE.md`.

## Backlog (flagged, not yet scheduled)

- **i18n / global adoption.** SeeQ's non-DX-mode candidate ranking (`qso.py`'s
  `rank_cqs`/`candidate_tier`, added 2026-07-25) already derives "home country" from the
  operator's own `MYCALL` prefix via `dxcc.country_for_call()` — never hardcoded to the
  US, works correctly for any operator's callsign out of the box. What's still
  US-centric is display polish only: `dashboard.py`'s `callLocation()`/`targetLatLon()`
  special-case `country==='United States'` to show state-from-grid and US call-area
  detail for US-based **contacts** (this is about the other station, not the operator,
  so it doesn't need a config key — it's just extra detail SeeQ can offer for any
  contact whose country happens to resolve to the US, regardless of where the operator
  is). Actual UI-string translation (station log messages, dashboard labels) hasn't
  been scoped at all — flagged here per Logan's 2026-07-25 note, not started.

## Execution notes

- **Order: M0a before any M1/M2 feature code.** Building JS8 straight into today's
  monolithic `dashboard.py` would be the abstraction-avoided-too-long case — one mode
  never needed a seam, two known, already-scoped future modes (JS8, Winlink email) does.
  M0b (the physical panel split) doesn't have to land first — it can happen as part of
  early M1 work, once JS8's actual panel needs force its cut points, rather than as a
  separate speculative pass beforehand.
- M2 no longer needs a research pass (see above — already decided, already documented in
  `skills/email-over-radio.md`), but still waits on M0a since it's a mode like any other.
  Given its `engine.py` is thin (mostly Pat+ardopcf process lifecycle, no new message
  state machine to design), M2 may turn out cheaper to build than M1 once M0a lands —
  worth reconsidering build order then rather than assuming M1-before-M2 strictly.
- **Same safety invariant as ROADMAP.md:** the TX safety chain is frozen code regardless of
  mode — no cheap-model or local-model session may modify watchdog, frequency verification,
  or attended gates in any mode's `engine.py` without full tests + Logan's explicit review.
