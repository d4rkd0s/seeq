#!/usr/bin/env python3
"""JS8 mode: the independent unkey watchdog.

Read this before changing anything in here.

FT8's watchdog (bin/qso.py) spawns a detached OS process running
`sleep <deadline>; rigctl ... T 0` immediately before every key-up. Its
independence is real and total: `rigctl` drives the serial port directly, so
the backstop keeps working even if qso.py is killed, hangs, or segfaults. The
watchdog and the thing it is guarding share nothing.

**JS8 cannot reproduce that, and this file does not pretend otherwise.**

In JS8 mode, JS8Call-improved -- not SeeQ -- owns the CAT port and performs the
keying. The strongest lever available is `RIG.TX_HALT`, which is a *request to
the very process whose misbehaviour is being guarded against*. So:

  What this watchdog DOES protect against
    - SeeQ's own engine.py dying, hanging, or losing its socket mid-send: this
      runs as a separate detached process on its own independent TCP
      connection, so a wedged command connection cannot wedge the backstop.
    - A transmission running past its frame deadline while JS8Call is still
      responsive -- the ordinary runaway case.
    - The dial drifting off the intended frequency mid-transmission.
    - A long TX queue keying the radio for longer than any single message
      should (absolute session cap).

  What it CANNOT protect against, stated plainly
    - JS8Call-improved itself hanging or crashing while keyed. If it stops
      servicing its socket, TX_HALT never lands, and SeeQ has no remaining
      path to the radio in this mode -- the CAT port belongs to that process.
      In that scenario the attended operator standing at the radio is not one
      backstop among several; it is the only one. This is a genuinely weaker
      guarantee than FT8's, and it is why JS8 TX stays gated behind explicit
      control-operator sign-off.

The mitigation for that residual risk is detection, not recovery: when the
connection goes stale the state file is marked so the dashboard can raise a
loud alarm, minimising the operator's time-to-notice.

Run standalone (this is how engine.py spawns it):
    python3 bin/modes/js8/watchdog.py --deadline 17 --expected-dial 7078000
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.dirname(os.path.dirname(_HERE))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
sys.path.insert(0, _HERE)
import api  # noqa: E402

# Absolute ceiling on one armed session, regardless of how many frames keep
# re-arming the per-frame deadline. Kept well under the 10-minute
# identification interval of 47 CFR 97.119 so that this cap can never be the
# reason an ID requirement gets marginal.
MAX_SESSION_S = 300.0

# Margin added on top of a frame's nominal TX duration before the watchdog
# considers it overrun: enough that normal jitter never trips it, small enough
# that a stuck carrier is caught quickly.
_MIN_MARGIN_S = 4.0
_MARGIN_FRACTION = 0.25

DEFAULT_DIAL_TOLERANCE_HZ = 100

STATE_FILENAME = "js8-watchdog.json"


def deadline_for_speed(speed):
    """Per-frame unkey deadline in seconds.

    FT8's single 14 s constant is tuned to its 15 s cycle and does not
    transfer: JS8 frames run 3.95 s (JS8 40) to 25.28 s (Slow). An unknown
    speed falls back to the most generous deadline -- cutting a legitimate
    transmission short is a worse default than waiting slightly longer to act.
    """
    frame = api.SPEED_TX_SECONDS.get(speed)
    if frame is None:
        frame = max(api.SPEED_TX_SECONDS.values())
    return frame + max(_MIN_MARGIN_S, frame * _MARGIN_FRACTION)


class WatchdogState:
    """Pure decision logic: fed events and a clock, says whether to halt.

    Kept free of sockets and sleeps so the multi-minute behaviour can be
    tested exhaustively in milliseconds.
    """

    def __init__(self, deadline_s, max_session_s=MAX_SESSION_S, started_at=0.0,
                 expected_dial=None, dial_tolerance_hz=DEFAULT_DIAL_TOLERANCE_HZ):
        self.deadline_s = deadline_s
        self.max_session_s = max_session_s
        self.started_at = started_at
        self.expected_dial = expected_dial
        self.dial_tolerance_hz = dial_tolerance_hz
        self.keyed_since = None
        self.frames = 0
        self.dial_fault = None

    def on_ptt(self, on, now):
        """A pushed RIG.PTT event. Each key-up re-arms the deadline: the unit
        being protected is the individual frame, not the whole message, because
        one TX.SEND_MESSAGE legitimately produces several PTT cycles."""
        if on:
            self.keyed_since = now
            self.frames += 1
        else:
            self.keyed_since = None

    def on_dial(self, dial, now):
        """Frequency read-back taken at key-up.

        Note this is *reactive*: JS8Call's own scheduler decides when each
        frame starts, so SeeQ observes the dial at key-up rather than gating
        before it. The first frame of a mistuned session will have gone out
        already; what this can still do is stop the rest.
        """
        if self.expected_dial is None or dial is None:
            return
        if abs(int(dial) - int(self.expected_dial)) > self.dial_tolerance_hz:
            self.dial_fault = f"dial reads {dial} Hz, expected {self.expected_dial} Hz"

    def check(self, now):
        """(should_halt, reason)."""
        if self.dial_fault and self.keyed_since is not None:
            return True, self.dial_fault
        if self.keyed_since is None:
            return False, ""
        if now - self.started_at > self.max_session_s:
            return True, (f"session cap: keyed activity has continued for more than "
                          f"{self.max_session_s:.0f}s")
        if now - self.keyed_since > self.deadline_s:
            return True, (f"frame deadline: PTT has been on for "
                          f"{now - self.keyed_since:.1f}s (limit {self.deadline_s:.1f}s)")
        return False, ""


def _write_state(state_path, obj):
    if not state_path:
        return
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, state_path)
    except OSError:
        pass


def run(client, deadline_s, max_session_s=MAX_SESSION_S, expected_dial=None,
        dial_tolerance_hz=DEFAULT_DIAL_TOLERANCE_HZ, clock_fn=time.monotonic,
        sleep_fn=time.sleep, max_iterations=None, state_path=None, log_fn=None):
    """Watch the pushed event stream and halt the transmitter if it overruns.

    `client` must be a connected client on its OWN socket, not shared with
    engine.py's command connection -- a request/response socket that wedges
    mid-read would otherwise take the safety path down with it.

    Returns {"halted": bool, "reason": str, "frames": int}.
    """
    started = clock_fn()
    wd = WatchdogState(deadline_s=deadline_s, max_session_s=max_session_s,
                        started_at=started, expected_dial=expected_dial,
                        dial_tolerance_hz=dial_tolerance_hz)

    def log(msg):
        if log_fn:
            log_fn(msg)

    iterations = 0
    stale = False
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        try:
            msg = client.read_event(timeout=1.0)
        except api.Js8ApiError as e:
            # Connection died. We cannot halt over a dead socket -- all that's
            # left is to make the loss loud and immediate for the operator.
            stale = True
            log(f"modes/js8 watchdog: connection lost while "
                f"{'KEYED' if wd.keyed_since is not None else 'idle'} ({e})")
            _write_state(state_path, {"armed": False, "stale": True, "keyed": wd.keyed_since is not None,
                                       "reason": f"connection lost: {e}", "frames": wd.frames})
            return {"halted": False, "reason": f"connection lost: {e}",
                    "frames": wd.frames, "stale": True}

        now = clock_fn()
        if msg is not None and msg.get("type") == "RIG.PTT":
            on = bool((msg.get("params") or {}).get("PTT"))
            wd.on_ptt(on, now)
            if on and expected_dial is not None:
                try:
                    dial, _offset = client.get_freq()
                    wd.on_dial(dial, now)
                except api.Js8ApiError as e:
                    log(f"modes/js8 watchdog: dial read-back failed ({e})")
        elif msg is not None and msg.get("type") == "STATION.CLOSING":
            log("modes/js8 watchdog: JS8Call reported a clean shutdown")
            break

        should_halt, reason = wd.check(now)
        if should_halt:
            log(f"modes/js8 watchdog: HALTING -- {reason}")
            halted = False
            for attempt in range(3):
                try:
                    client.tx_halt()
                    halted = True
                    break
                except api.Js8ApiError as e:
                    log(f"modes/js8 watchdog: TX_HALT attempt {attempt + 1} failed ({e})")
                    sleep_fn(0.5)
            _write_state(state_path, {"armed": False, "stale": not halted, "keyed": False,
                                       "reason": reason, "halted": halted, "frames": wd.frames})
            return {"halted": halted, "reason": reason, "frames": wd.frames, "stale": not halted}

        _write_state(state_path, {"armed": wd.keyed_since is not None, "stale": False,
                                   "keyed": wd.keyed_since is not None,
                                   "frames": wd.frames, "reason": ""})
        if now - started > max_session_s and wd.keyed_since is None:
            break

    _write_state(state_path, {"armed": False, "stale": stale, "keyed": False,
                               "frames": wd.frames, "reason": "completed"})
    return {"halted": False, "reason": "completed", "frames": wd.frames, "stale": stale}


def main(argv=None):
    ap = argparse.ArgumentParser(description="JS8 independent unkey watchdog")
    ap.add_argument("--port", type=int, default=api.DEFAULT_PORT)
    ap.add_argument("--deadline", type=float, required=True,
                    help="per-frame unkey deadline in seconds")
    ap.add_argument("--max-session", type=float, default=MAX_SESSION_S)
    ap.add_argument("--expected-dial", type=int, default=None)
    ap.add_argument("--tolerance", type=int, default=DEFAULT_DIAL_TOLERANCE_HZ)
    ap.add_argument("--state", default=None, help="path for the status JSON file")
    args = ap.parse_args(argv)

    def log(msg):
        print(msg, flush=True)

    client = api.Js8Client(port=args.port, timeout=5.0)
    try:
        client.connect()
    except api.Js8ApiError as e:
        log(f"modes/js8 watchdog: cannot arm -- {e}")
        _write_state(args.state, {"armed": False, "stale": True,
                                   "reason": f"could not connect: {e}"})
        return 1
    try:
        result = run(client, deadline_s=args.deadline, max_session_s=args.max_session,
                      expected_dial=args.expected_dial, dial_tolerance_hz=args.tolerance,
                      state_path=args.state, log_fn=log)
    finally:
        client.close()
    return 0 if not result.get("stale") else 1


if __name__ == "__main__":
    sys.exit(main())
