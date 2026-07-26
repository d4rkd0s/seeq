"""JS8 mode: TX lifecycle wrapper.

Deliberately NOT a copy of bin/qso.py. FT8's chaser owns a protocol state
machine (a fixed four-phase exchange) because SeeQ implements FT8's calling
logic itself. In JS8 mode JS8Call-improved is the protocol engine -- it owns
the directed-call grammar, heartbeats, relay, store-and-forward and the
waveform. What is left for SeeQ, and all this module does, is the part that
matters for safety and control:

  1. Refuse to transmit unless the control operator explicitly confirmed it.
  2. Read the dial back and check it against both the intended JS8 calling
     frequency and the operator's licence privileges, before anything is
     queued.
  3. Arm the independent watchdog *before* the message goes out.
  4. Provide an immediate halt.

Point 3 mirrors bin/qso.py, which arms its detached unkey subprocess before
every key-up rather than after -- arming afterwards leaves a window in which a
transmission is live with no backstop. The JS8 watchdog is weaker than FT8's
for reasons spelled out in watchdog.py's docstring; that weakness is the
reason JS8 TX stays behind explicit control-operator sign-off, and it is not
papered over anywhere in this code.
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.dirname(os.path.dirname(_HERE))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
import band_plan  # noqa: E402
import dashboard  # noqa: E402

sys.path.insert(0, _HERE)
import api  # noqa: E402
import pipeline  # noqa: E402
import watchdog as wd  # noqa: E402

WATCHDOG_PY = os.path.join(_HERE, "watchdog.py")
WATCHDOG_STATE = os.path.join(dashboard.DATA, "js8-watchdog.json")

# JS8 calling frequencies (USB dial), from ~/Radio/skills/js8.md. Every entry
# is inside General-class data privileges with room for the full audio
# passband on top -- there's a test that re-checks that against
# bin/band_plan.py rather than trusting this comment.
#
# Bands absent from this table are absent on purpose: send() fails closed for
# them rather than guessing a dial, since a guess is how a signal ends up
# outside a band edge.
JS8_CALLING_HZ = {
    "80m": 3578000,
    "40m": 7078000,
    "30m": 10130000,
    "20m": 14078000,
    "15m": 21078000,
    "10m": 28078000,
}

# Widest audio offset JS8Call will place a signal at; the emitted frequency is
# dial + offset, so privilege checks have to allow for it.
MAX_OFFSET_HZ = 2500

DIAL_TOLERANCE_HZ = 100


def _conf():
    return dashboard._C


def expected_dial(conf=None):
    """(dial_hz, band, error). Fails closed for an untabled band."""
    conf = conf if conf is not None else _conf()
    band = (conf.get("BAND") or "").strip().lower()
    if not band:
        return None, band, "BAND is not set in station.conf"
    dial = JS8_CALLING_HZ.get(band)
    if dial is None:
        return None, band, (f"no JS8 calling frequency is tabled for {band} "
                             f"(known: {', '.join(sorted(JS8_CALLING_HZ))})")
    return dial, band, None


def check_dial(actual_dial, conf=None):
    """(ok, detail) -- the pre-transmit frequency gate.

    Two independent checks, both of which must pass: the radio is where we
    intended it to be, and where it is happens to be legal. Reuses
    bin/band_plan.py's privilege tables rather than restating the band edges
    here, so there is only one copy to keep correct.
    """
    conf = conf if conf is not None else _conf()
    want, band, err = expected_dial(conf)
    if err:
        return False, err
    if actual_dial is None:
        return False, "could not read the dial back from JS8Call"
    if abs(int(actual_dial) - want) > DIAL_TOLERANCE_HZ:
        return False, (f"dial reads {actual_dial} Hz, expected the {band} JS8 "
                        f"calling frequency {want} Hz")
    lic = (conf.get("LICENSE_CLASS") or "general").strip().lower()
    for probe in (int(actual_dial), int(actual_dial) + MAX_OFFSET_HZ):
        ok, detail = band_plan.check_frequency(lic, band, probe, "data")
        if not ok:
            return False, detail
    return True, f"dial {actual_dial} Hz verified for {band} data"


def _client(client_fn=None):
    if client_fn:
        return client_fn()
    return api.Js8Client(port=pipeline.tcp_port(), timeout=5.0)


def _arm_watchdog(speed, dial, spawn_fn):
    """Spawn the detached watchdog on its own connection, pre-armed."""
    deadline = wd.deadline_for_speed(speed)
    cmd = [sys.executable, WATCHDOG_PY,
           "--port", str(pipeline.tcp_port()),
           "--deadline", f"{deadline:.2f}",
           "--max-session", f"{wd.MAX_SESSION_S:.0f}",
           "--tolerance", str(DIAL_TOLERANCE_HZ),
           "--state", WATCHDOG_STATE]
    if dial is not None:
        cmd += ["--expected-dial", str(int(dial))]
    spawn_fn(cmd, os.path.join(dashboard.DATA, "js8-watchdog.log"))
    return deadline


def send(text, confirm=False, dryrun=False, *, client_fn=None, spawn_fn=None, conf=None):
    """Queue one JS8 message for transmission. (ok, detail).

    Every refusal path here is deliberate; none of them should be relaxed
    without control-operator review.
    """
    conf = conf if conf is not None else _conf()
    spawn_fn = spawn_fn or dashboard._spawn_detached

    text = (text or "").strip()
    if not text:
        return False, "nothing to send"
    if not confirm:
        # CLAUDE.md rule 1: never key PTT autonomously. The dashboard's compose
        # widget sets this only from a real operator click.
        return False, "confirm required -- JS8 transmit needs an explicit operator confirmation"

    if dryrun or dashboard.DRYRUN:
        dashboard.log_action(f"[DRYRUN] would transmit JS8: {text!r}")
        return True, f"[DRYRUN] not transmitted: {text}"

    client = None
    try:
        client = _client(client_fn)
        client.connect()

        # --- frequency read-back, before anything is queued ---------------
        dial, offset = client.get_freq()
        ok, detail = check_dial(dial, conf)
        if not ok:
            dashboard.log_action(f"modes/js8 send: REFUSED -- {detail}")
            return False, detail

        speed = client.get_speed()

        # --- arm the backstop BEFORE the transmission, never after --------
        deadline = _arm_watchdog(speed, dial, spawn_fn)

        client.send_message(text)
        dashboard.log_action(
            f"modes/js8 send: queued {text!r} @ dial {dial} Hz offset {offset} Hz, "
            f"speed {api.SPEED_NAMES.get(speed, speed)}, watchdog {deadline:.1f}s/frame")
        return True, (f"queued at {dial} Hz ({api.SPEED_NAMES.get(speed, speed)}), "
                       f"watchdog armed at {deadline:.1f}s per frame")

    except api.Js8ApiError as e:
        # We may or may not have keyed. Assume the worse of the two and try to
        # stand the transmitter down.
        dashboard.log_action(f"modes/js8 send: FAILED ({e}) -- attempting halt")
        if client is not None:
            try:
                client.tx_halt()
            except Exception:
                pass
        return False, f"send failed: {e}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def halt(client_fn=None):
    """Immediate transmitter halt. (ok, detail).

    Reports failure honestly rather than claiming success -- a STOP control
    that lies about having worked is worse than one that admits it couldn't
    reach the radio, because it stops the operator from escalating.
    """
    client = None
    try:
        client = _client(client_fn)
        client.connect()
        client.tx_halt()
        dashboard.log_action("modes/js8: RIG.TX_HALT sent")
        return True, "TX halt sent"
    except Exception as e:
        dashboard.log_action(f"modes/js8: RIG.TX_HALT FAILED ({e!r})")
        return False, (f"could not halt via the JS8Call API ({e}) -- "
                        f"unkey at the radio")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def set_speed(speed, client_fn=None):
    # Validate before opening a socket. api.Js8Client.set_speed checks this too,
    # but the codes are non-sequential (0/1/2/4/8 -- 3 is not a speed), so a bad
    # value should be a clean refusal here rather than a round-trip that ends in
    # an exception, and the dashboard endpoint gets the same guarantee.
    if speed not in api.SPEED_NAMES:
        return False, (f"{speed} is not a JS8 speed code (valid: "
                        f"{', '.join(f'{k}={v}' for k, v in sorted(api.SPEED_NAMES.items()))})")
    client = None
    try:
        client = _client(client_fn)
        client.connect()
        client.set_speed(speed)
        dashboard.log_action(f"modes/js8: speed -> {api.SPEED_NAMES.get(speed, speed)}")
        return True, f"speed set to {api.SPEED_NAMES.get(speed, speed)}"
    except (api.Js8ApiError, ValueError) as e:
        return False, str(e)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def inbox_store(callsign, text, client_fn=None):
    """Store a message for another station to collect (JS8's store-and-forward).
    Does not transmit -- it only puts the message in the local inbox."""
    client = None
    try:
        client = _client(client_fn)
        client.connect()
        msg_id = client.inbox_store(callsign, text)
        dashboard.log_action(f"modes/js8: stored inbox message for {callsign}")
        return True, f"stored (id {msg_id})"
    except api.Js8ApiError as e:
        return False, str(e)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def status(client_fn=None):
    """Snapshot for the dashboard's TX panel. Never raises."""
    out = {"ptt": False, "queue_depth": 0, "speed": None, "speed_name": None,
           "dial": None, "offset": None, "dial_ok": None, "dial_detail": "",
           "watchdog": _watchdog_state(), "api": False}
    client = None
    try:
        client = _client(client_fn)
        client.connect()
        out["api"] = True
        out["ptt"], _msg = client.get_ptt()
        out["queue_depth"] = client.queue_depth()
        speed = client.get_speed()
        out["speed"] = speed
        out["speed_name"] = api.SPEED_NAMES.get(speed, str(speed))
        dial, offset = client.get_freq()
        out["dial"], out["offset"] = dial, offset
        out["dial_ok"], out["dial_detail"] = check_dial(dial)
    except Exception as e:
        out["error"] = str(e)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return out


def _watchdog_state():
    try:
        import json
        with open(WATCHDOG_STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# -- mode_switch/dashboard contract parity with modes/ft8/engine.py ---------
def chase_start(body, dryrun=False):
    """JS8 has no unattended 'chase N QSOs' concept, and won't get one without
    a separate control-operator decision -- JS8 is a conversational mode, so
    'work N stations automatically' would mean autonomously composing free
    text, which is a much bigger ask than FT8's fixed exchange."""
    return False, "JS8 mode has no automatic chase -- compose and send messages individually"


def chase_stop(dryrun=False):
    return halt()[0] and {"stopped": True} or {"stopped": False}
