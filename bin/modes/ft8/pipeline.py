"""FT8 mode: RX lifecycle wrapper for bin/mode_switch.py.

Per the M0 plan's Q1 decision ("wrap, don't move"), this does NOT reimplement
any of bin/rx-loop.sh's or bin/qso.py's logic -- it reuses bin/dashboard.py's
already-tested process helpers (_spawn_detached/_pkill/_proc_running) and
constants (QSO_PY/RXLOOP_SH/RIG_MODEL/CAT_PORT/CAT_BAUD) exactly as dashboard.py's
own _action_rx_start/_action_rx_stop already do. dashboard.py is safe to import
as a plain module -- server startup is gated behind `if __name__=="__main__"`,
same pattern tools/test_dashboard_actions.py and tools/test_dashboard_js.py
already rely on.
"""
import os
import subprocess
import sys

_BIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
import dashboard  # noqa: E402


def start(dryrun=False):
    """Mirrors dashboard._action_rx_start's body exactly."""
    if dryrun:
        dashboard.log_action(
            f"[DRYRUN] would start rx-loop: bash {dashboard.RXLOOP_SH} "
            f">> {dashboard.DATA}/rx-loop.log 2>&1 &")
        return {"started": True, "dryrun": True}
    if dashboard._proc_running(dashboard.RXLOOP_SH):
        dashboard.log_action("modes/ft8: rx already running, no-op")
        return {"started": False, "already": True}
    dashboard._spawn_detached(["bash", dashboard.RXLOOP_SH],
                               os.path.join(dashboard.DATA, "rx-loop.log"))
    dashboard.log_action(f"modes/ft8: spawned bash {dashboard.RXLOOP_SH}")
    return {"started": True}


def stop(dryrun=False):
    """Mirrors dashboard._action_rx_stop's body exactly: rigctl T 0 fires
    first and unconditionally, then the chaser is killed, then rx-loop."""
    if dryrun:
        dashboard.log_action(
            f"[DRYRUN] would stand down: rigctl T 0; pkill -f {dashboard.QSO_PY}; "
            f"pkill -f {dashboard.RXLOOP_SH}")
        return {"stopped": True, "dryrun": True}
    try:
        subprocess.run(["rigctl", "-m", dashboard.RIG_MODEL, "-r", dashboard.CAT_PORT,
                         "-s", dashboard.CAT_BAUD, "T", "0"],
                        capture_output=True, text=True, timeout=10)
    except Exception as e:
        dashboard.log_action(f"modes/ft8 stop: rigctl T 0 error: {e!r}")
    killed_chaser = dashboard._pkill(dashboard.QSO_PY)
    ok = dashboard._pkill(dashboard.RXLOOP_SH)
    dashboard.log_action(f"modes/ft8 stop: rigctl T 0 (sent first); "
                          f"pkill -f {dashboard.QSO_PY} -> {killed_chaser}; "
                          f"pkill -f {dashboard.RXLOOP_SH} -> {ok}")
    return {"stopped": ok, "chaser_killed": killed_chaser}


def is_running():
    return dashboard._proc_running(dashboard.RXLOOP_SH) or dashboard._proc_running(dashboard.QSO_PY)


def _hardware_ready():
    """(ok, detail) -- CAT port present, PTT reads 0. Shared by sanity_check()
    and preflight() below; never sends T 1, only ever reads state or sends
    the same unconditional T 0 dashboard.py's own STOP+UNKEY action already
    sends."""
    if not os.path.exists(dashboard.CAT_PORT):
        return False, f"CAT port missing ({dashboard.CAT_PORT})"
    try:
        r = subprocess.run(["rigctl", "-m", dashboard.RIG_MODEL, "-r", dashboard.CAT_PORT,
                             "-s", dashboard.CAT_BAUD, "t"],
                            capture_output=True, text=True, timeout=10)
        ptt = r.stdout.strip()
    except Exception as e:
        return False, f"PTT read-back failed: {e!r}"
    if ptt != "0":
        return False, f"PTT reads '{ptt}', expected 0"
    return True, "clear"


def sanity_check():
    """(ok, detail) -- confirms a mode we just told to stop actually did:
    hardware ready AND no stray qso.py/rx-loop.sh process. Used by
    bin/mode_switch.py's changeover after calling stop()."""
    if is_running():
        return False, "qso.py or rx-loop.sh still running"
    return _hardware_ready()


def preflight():
    """(ok, detail) -- confirms hardware is ready to start this mode. Does
    NOT check is_running(): if this mode's own process already happens to be
    running (e.g. coa start's unconditional rx-loop autostart beat the
    changeover to it), that's not a failure -- start() is already a no-op in
    that case, same as dashboard.py's existing _action_rx_start. Used by
    bin/mode_switch.py's changeover for a boot (no prior mode) target."""
    return _hardware_ready()
