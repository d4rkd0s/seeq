# M0 (mode abstraction) — open questions before implementation

Companion to [MODES-ROADMAP.md](MODES-ROADMAP.md) Phase M0. Each question below is a
real fork I found while reading `bin/seeq`, `bin/dashboard.py`, and `bin/qso.py` to scope
the work — not filler. Each has my recommendation and *why*, plus a blank for your
answer. Fill in as many as you want, in any order; leave the rest blank and I'll go with
the recommendation. I won't start writing M0 code until I've checked this file for your
answers.

Two of these (Q1, Q2) touch the frozen TX-safety path (frequency read-back / PTT
control) — per `CLAUDE.md`, those need your explicit review before any change, not just
my judgment call, which is why they're here instead of decided silently.

---

## Q1 — Migrate FT8's code into `bin/modes/ft8/`, or wrap it?

**What I found:** `bin/qso.py` (859 lines), `bin/rx-loop.sh`, and `bin/parse_decodes.py`
are the existing FT8 engine/pipeline — tested, safety-critical (watchdog, frequency
read-back, PTT), and currently untouched by any of this session's map/UI work. M0.6 says
"migrate today's FT8 code into `bin/modes/ft8/` as the reference implementation."

**The fork:**
- **(a) Move** — physically relocate the files/logic into `bin/modes/ft8/pipeline.py`
  and `engine.py`. A "truer" refactor, but it means editing safety-critical code just to
  relocate it, with all the diff-review risk that implies.
- **(b) Wrap** — leave `bin/qso.py`, `bin/rx-loop.sh`, `bin/parse_decodes.py` exactly
  where they are, byte-for-byte untouched. `bin/modes/ft8/pipeline.py`/`engine.py`
  become thin adapters that shell out to them, the same way `dashboard.py` already
  does today (`_spawn_detached(["bash", RXLOOP_SH], ...)`, `_spawn_detached(args,
  CHASELOG)` where `args` builds a `qso.py` command line). Proves the mode-abstraction
  seam works without touching a single line of frozen code.

**My recommendation: (b) wrap.** Zero risk to the frozen TX-safety chain, and it still
fully satisfies "prove the abstraction with a real mode" — the abstraction is about
*dashboard/seeq's* relationship to a mode, not about where `qso.py`'s internals live. An
actual code-move (if ever wanted) becomes its own separate, later, non-M0 decision.

**Your answer:** ___________________________________________________

b - i agree, b.

---

## Q2 — Persistent `rigctld`, or keep today's per-call `rigctl`?

**What I found:** Today, every CAT command in `dashboard.py`/`qso.py` spawns a fresh
`rigctl -m $RIG_MODEL -r $CAT_PORT -s $CAT_BAUD ...` subprocess per call — no daemon.
But `skills/email-over-radio.md` (already-decided Winlink design, M2) explicitly assumes
a **long-lived `rigctld`** that both Pat and WSJT-X share over TCP (`localhost:4532`),
specifically so two programs don't fight over the raw serial port.

**The fork:**
- **(a) Introduce `rigctld` now, in M0** — all modes (including FT8) talk to the rig
  over TCP through one shared daemon. Makes "CAT port free" in the M0.3 sanity check
  mean "no mode holds an active rigctld session," not "raw serial device unheld" —
  arguably a cleaner invariant. But it means changing how FT8's frequency read-back and
  PTT calls work, which is exactly the frozen-code path.
- **(b) Leave FT8 on direct per-call `rigctl` for M0`** — don't touch the frozen path
  speculatively. M0's sanity check defines "CAT free" the way `bin/seeq`'s existing
  `preflight()` already does (`[ -e "$CAT_PORT" ]` + no process holding it). Revisit
  `rigctld` concretely when M2 (email) actually gets built, since Pat/ardopcf's need
  for it is real and immediate, vs. JS8's is still unconfirmed (Q from
  `MODES-ROADMAP.md` M1.2 — don't know JS8Call-improved's control API yet).

**My recommendation: (b).** Don't change frozen TX-safety code as a side effect of an
unrelated refactor. Let the mode that actually needs a daemon (M2) introduce it, scoped
to that mode's own `pipeline.py`, and decide then whether FT8 should also move onto it.

**Your answer:** ___________________________________________________

b, leave a for later, js8 will be a though now, but leaving ft8 on legacy until it can be upgraded to the daemon later

---

## Q3 — How long should the "poll until stopped" step wait before giving up?

**Context:** Your instruction was explicit: mode switching should power down the current
mode fully, *wait* until it's actually stopped, sanity-check, then switch — not an
instant swap. `qso.py` has no `SIGTERM` handler (relies on the OS default + its own
independent per-frame watchdog, a detached `sleep {WATCHDOG_S}s; rigctl T 0` subprocess
that fires even if `qso.py` itself dies immediately). Worst case, a `pkill` lands mid-TX-
frame and the *rig* doesn't confirm fully idle until that frame's watchdog fires — up to
`WATCHDOG_S` (14 s default) after the kill signal.

**The question:** how long should the changeover's poll loop wait before declaring the
old mode "stuck" and aborting the switch (surfacing an error, not silently proceeding —
that part isn't in question, a failed sanity check always hard-aborts the switch)?

**My recommendation:** ~30–45 s — comfortably past one worst-case watchdog cycle plus
process-exit time, generous enough to avoid false "stuck" aborts on a normal teardown,
short enough that a genuinely stuck process surfaces quickly. Exact number is a feel
thing more than an engineering one, which is why I'm asking rather than just picking.

**Your answer:** ___________________________________________________
yes a 30-45 sec swap, so the HAM is deliberate in mode switching
---

## Q4 — Does `seeq start` (CLI) need the boot chooser too, or is that dashboard-only?

**What I found:** `seeq start` today is non-interactive/scriptable — it runs `preflight`,
then unconditionally spawns `dashboard.py` **and** `rx-loop.sh` (i.e., today `seeq start`
= "dashboard up + FT8 RX running," no prompt, no mode concept). Under M0, RX-loop
start-up becomes FT8-mode-specific (`bin/modes/ft8/pipeline.py`), so `seeq start`
auto-starting it unconditionally would silently pick FT8 for you — exactly the "silent
default" your boot-chooser instruction was against.

**The fork:**
- **(a) Chooser is dashboard-only.** `seeq start` changes to: preflight (hardware/audio/
  clock only, no mode-specific checks) + spawn `dashboard.py` alone, landing on the
  "Welcome — select a mode to begin" screen; **no pipeline auto-starts**. Mode selection
  (and thus starting any mode's pipeline) only happens by picking one in the browser,
  which runs the same M0.3 changeover. `seeq start` stays scriptable/non-interactive.
- **(b) `seeq start` also prompts.** The CLI itself gains an interactive mode-select step
  (or a required `seeq start --mode ft8` flag), so a fully headless `seeq start` either
  blocks on input or fails without an explicit mode argument — breaking any existing
  scripted/cron use of plain `seeq start`.

**My recommendation: (a).** Keeps `seeq start` scriptable (matches `seeq logsync`/`seeq
report`'s existing non-interactive design), and "app boot shows a chooser" is naturally
a statement about the dashboard (the thing with a UI to show a chooser *in*), not the
shell entrypoint. This does mean `seeq start` alone will no longer get you FT8 RX running
the way it does today — you'll always pick the mode in the browser afterward.

**Your answer:** ___________________________________________________
I don't fully get this one so let's do a. 
but if you think this is still worthy to look into, add to later in the roadmap 
---

## Q5 — Does `seeq chase N` still work as a direct CLI shortcut?

**What I found:** `seeq chase N` today bypasses the dashboard/UI entirely — it calls
`"$0" start` (preflight + spawn dashboard/rx-loop) then runs `qso.py` directly in the
foreground, Ctrl-C-safe (`trap 'unkey; ...' INT TERM`). It's a fast, scriptable, terminal-
only path — no browser needed.

**The fork:**
- **(a) Keep `seeq chase N` exactly as-is**, unchanged by M0 — it's a direct CLI
  operator tool, not "the app," so the deliberate-changeover/chooser rules (which are
  about *mode switching* in a running system with a UI) don't apply to it. It implicitly
  always means FT8 chasing, same as today.
- **(b) Route `seeq chase N` through `seeq mode switch ft8` first**, so even a quick
  terminal chase gets the full polled stop-current/sanity-check/preflight sequence
  before running — consistent with every other path into a mode, but adds real wall-
  clock delay (per Q3) to a command you may want to fire off fast.

**My recommendation: (a).** This is a terminal power-user shortcut you reach for when
you want speed, not a "UI mode switch." Since it already does its own `preflight` via
`"$0" start`, it's not skipping safety — just skipping the multi-mode changeover
machinery that only matters once more than one mode's pipeline actually exists to be
"switched away from."

**Your answer:** ___________________________________________________
a, but warn harder / show red/yellow and alert the user when they are stepping into areas where they should be more focused. 
---

## How I'll proceed

Once you've filled in what you want to weigh in on (or told me to just go with the
recommendations), I'll write the actual M0 implementation plan — file-by-file, with the
TDD red→green sequence per `CLAUDE.md` — before touching code, same as the earlier DX
Mode extensions plan. Q1/Q2 in particular gate how much of `qso.py`/`dashboard.py`'s
existing rig-control code the plan touches at all.
