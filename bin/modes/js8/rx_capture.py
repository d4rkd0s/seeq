#!/usr/bin/env python3
"""JS8 mode: the long-lived decode capture process.

FT8's equivalent is bin/rx-loop.sh: a separate, detached, always-running
process whose only job is to turn what the radio hears into files the
dashboard can read. This is that, for JS8 -- except the decoder is
JS8Call-improved itself, so instead of driving `jt9` on a 15-second cycle
this holds a TCP connection open and consumes the events JS8Call pushes.

Receive only. Nothing in this file transmits, keys PTT, or writes to the API
beyond an initial PING to wake it up (documented as required in API.md). The
one write it does perform is to disk.

Outputs, both under data/:
  js8/decodes/YYYY-MM-DD/HH.jsonl   append-only event log, rotated hourly,
                                    mirroring FT8's decode storage layout
  js8-state.json                    rolling snapshot the dashboard polls

Run standalone for debugging: python3 bin/modes/js8/rx_capture.py
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.dirname(os.path.dirname(_HERE))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
import dashboard  # noqa: E402

sys.path.insert(0, _HERE)
import api  # noqa: E402
import pipeline  # noqa: E402

DECODE_DIR = os.path.join(dashboard.DATA, "js8", "decodes")
STATE_JSON = os.path.join(dashboard.DATA, "js8-state.json")

# How much scrollback the dashboard panel gets. JS8 conversations are slow --
# a few dozen entries is many minutes of band activity.
MAX_DIRECTED = 60
MAX_ACTIVITY = 120

RECONNECT_WAIT_S = 5.0


def event_record(msg, now_fn=time.time):
    """Normalize a pushed API event into a flat record, or None if it isn't
    one we log. Pure -- the parsing rules are the part worth testing."""
    typ = msg.get("type")
    if typ not in ("RX.ACTIVITY", "RX.DIRECTED", "RX.SPOT", "RIG.PTT"):
        return None
    p = msg.get("params") or {}
    rec = {
        "type": typ,
        "utc": int(p.get("UTC") or now_fn() * 1000),
        "text": msg.get("value", ""),
    }
    for src, dst in (("FROM", "from"), ("TO", "to"), ("CALL", "call"),
                      ("GRID", "grid"), ("SNR", "snr"), ("OFFSET", "offset"),
                      ("DIAL", "dial"), ("FREQ", "freq"), ("SPEED", "speed"),
                      ("CMD", "cmd"), ("TEXT", "msg")):
        if src in p:
            val = p[src]
            rec[dst] = val.strip() if isinstance(val, str) else val
    if typ == "RIG.PTT":
        rec["ptt"] = bool(p.get("PTT"))
    return rec


def roll(state, rec):
    """Fold one record into the rolling snapshot. Pure."""
    state = dict(state)
    state["last_event_utc"] = rec.get("utc")
    if rec["type"] == "RIG.PTT":
        state["ptt"] = rec.get("ptt", False)
        return state
    if rec["type"] == "RX.DIRECTED":
        directed = list(state.get("directed", []))
        directed.append(rec)
        state["directed"] = directed[-MAX_DIRECTED:]
    activity = list(state.get("activity", []))
    activity.append(rec)
    state["activity"] = activity[-MAX_ACTIVITY:]
    if rec.get("from") or rec.get("call"):
        heard = dict(state.get("heard", {}))
        call = rec.get("from") or rec.get("call")
        heard[call] = {"utc": rec.get("utc"), "snr": rec.get("snr"),
                        "grid": rec.get("grid", ""), "offset": rec.get("offset")}
        state["heard"] = heard
    return state


def decode_path(now=None):
    t = time.gmtime(now if now is not None else time.time())
    day = time.strftime("%Y-%m-%d", t)
    hour = time.strftime("%H", t)
    return os.path.join(DECODE_DIR, day, f"{hour}.jsonl")


def append_decode(rec, path=None):
    path = path or decode_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def initial_state():
    return {"directed": [], "activity": [], "heard": {}, "ptt": False,
            "connected": False, "last_event_utc": None}


def run(state_path=STATE_JSON, stop_after=None, sleep_fn=time.sleep):
    """Connect, consume pushes forever, reconnecting when the socket drops.

    `stop_after` (an int) bounds the loop for tests; production passes None.
    A dropped connection is expected, not exceptional -- JS8Call gets closed,
    restarted, or crashes, and this process should survive all three and
    reattach. It is deliberately NOT the safety watchdog: engine.py owns that,
    on its own separate connection, for the reasons in its module docstring.
    """
    state = initial_state()
    handled = 0
    while stop_after is None or handled < stop_after:
        client = api.Js8Client(port=pipeline.tcp_port(), timeout=5.0)
        try:
            client.connect()
        except api.Js8ApiError:
            state["connected"] = False
            dashboard.atomic_write_json(state_path, state)
            if stop_after is not None:
                return state
            sleep_fn(RECONNECT_WAIT_S)
            continue
        state["connected"] = True
        dashboard.atomic_write_json(state_path, state)
        client.ping()
        try:
            while stop_after is None or handled < stop_after:
                msg = client.read_event(timeout=30.0)
                if msg is None:
                    continue  # quiet band, not a problem
                if msg.get("type") == "STATION.CLOSING":
                    dashboard.log_action("modes/js8 capture: JS8Call is shutting down")
                    break
                rec = event_record(msg)
                if rec is None:
                    continue
                append_decode(rec)
                state = roll(state, rec)
                state["connected"] = True
                dashboard.atomic_write_json(state_path, state)
                handled += 1
        except api.Js8ApiError as e:
            dashboard.log_action(f"modes/js8 capture: connection lost ({e})")
        finally:
            client.close()
        state["connected"] = False
        dashboard.atomic_write_json(state_path, state)
        if stop_after is None:
            sleep_fn(RECONNECT_WAIT_S)
    return state


if __name__ == "__main__":
    run()
