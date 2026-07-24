"""Client for bandpulse.net's public v1Conditions API (docs: bandpulse.net/docs)
-- live per-band HF propagation conditions, used to show a top-3-bands banner
on the dashboard. Network touchpoint is curl via subprocess, same convention
as bin/qrz_api.py's post() -- no requests/urllib3 dependency.

Per bandpulse.net/docs: no auth required, CORS open, server-side cached
~5 min per grid cell, rate limit 30 req/5min/IP. We keep our own cache on
top of that so multiple dashboard clients polling us never hammer theirs.
"""
import json
import subprocess
import time

API_URL = "https://bandpulse.net/functions/v1Conditions"
USER_AGENT = "SeeQ-dashboard/1.0 (+https://github.com/d4rkd0s/seeq)"
CACHE_TTL_S = 300  # matches bandpulse.net's own server-side cache window

_state = {}  # grid -> {"last_attempt": epoch, "last_ok": bool, "last_result": data_or_err, "last_good": data_or_None}


def _curl(url, timeout=10):
    """The single network touchpoint: GET via curl. Returns (ok, text) --
    ok False means transport failure, text is the error."""
    try:
        r = subprocess.run(["curl", "-s", "-S", "-A", USER_AGENT,
                            "--max-time", str(timeout), url],
                           capture_output=True, text=True, timeout=timeout + 5)
    except FileNotFoundError:
        return False, "curl not found on this system"
    except subprocess.TimeoutExpired:
        return False, "request timed out"
    if r.returncode != 0:
        return False, f"curl exit {r.returncode}: {r.stderr.strip()}"
    return True, r.stdout.strip()


def fetch_conditions(grid, timeout=10):
    """Fetch live conditions for a Maidenhead grid. Returns (ok, data_or_errmsg)."""
    if not grid:
        return False, "grid required"
    url = f"{API_URL}?grid={grid}"
    ok, text = _curl(url, timeout=timeout)
    if not ok:
        return False, text
    try:
        data = json.loads(text)
    except ValueError:
        return False, "bad JSON from bandpulse.net"
    if "bands" not in data:
        return False, data.get("error", "unexpected response shape")
    return True, data


def top_bands(data, n=3):
    """Pure: top N bands by score descending, trimmed to what the banner
    needs. data is a parsed v1Conditions response."""
    bands = sorted(data.get("bands", []), key=lambda b: b.get("score", 0), reverse=True)
    return [{"id": b["id"], "name": b["name"], "state": b["state"],
              "label": b["label"], "score": b["score"]} for b in bands[:n]]


def get_cached_or_fetch(grid, clock_fn=time.time, fetch_fn=None):
    """Returns (ok, data_or_errmsg). Serves a cached copy when younger than
    CACHE_TTL_S, and fails soft: a transient fetch failure re-serves the
    last known-good data (stale) instead of blanking the banner, but only
    ever re-attempts the network once per TTL window either way."""
    fetch_fn = fetch_fn or fetch_conditions
    now = clock_fn()
    st = _state.setdefault(grid, {"last_attempt": None, "last_good": None})
    if st["last_attempt"] is not None and (now - st["last_attempt"]) < CACHE_TTL_S:
        if st["last_ok"]:
            return True, st["last_result"]
        if st["last_good"] is not None:
            return True, st["last_good"]
        return False, st["last_result"]
    ok, result = fetch_fn(grid)
    st["last_attempt"] = now
    st["last_ok"] = ok
    st["last_result"] = result
    if ok:
        st["last_good"] = result
        return True, result
    if st["last_good"] is not None:
        return True, st["last_good"]
    return False, result
