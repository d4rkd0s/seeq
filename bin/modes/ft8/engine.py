"""FT8 mode: TX/chase lifecycle wrapper for bin/mode_switch.py and dashboard.py.

Per Q1 ("wrap, don't move"), this reuses bin/dashboard.py's already-tested
_build_chase_args validation and process helpers exactly as
dashboard._action_chase_start/_action_chase_stop already do -- no logic from
bin/qso.py is duplicated or modified here.
"""
import os
import sys

_BIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
import dashboard  # noqa: E402


def chase_start(body, dryrun=False):
    """Mirrors dashboard._action_chase_start's body exactly. Returns
    (ok, result_dict_or_errmsg)."""
    args, desc, err = dashboard._build_chase_args(body)
    if err:
        return False, err
    if dryrun:
        dashboard.log_action(f"[DRYRUN] would start chaser: {' '.join(args)} (>> {dashboard.CHASELOG})")
        return True, {"started": True, "dryrun": True}
    if dashboard._proc_running(dashboard.QSO_PY):
        dashboard.log_action("modes/ft8 chase_start: refused, chaser already running")
        return False, "chaser already running"
    rx_autostarted = False
    if not dashboard._proc_running(dashboard.RXLOOP_SH):
        dashboard._spawn_detached(["bash", dashboard.RXLOOP_SH],
                                   os.path.join(dashboard.DATA, "rx-loop.log"))
        dashboard.log_action(f"modes/ft8 chase_start: rx-loop wasn't running, "
                              f"auto-started bash {dashboard.RXLOOP_SH}")
        rx_autostarted = True
    dashboard._spawn_detached(args, dashboard.CHASELOG)
    dashboard.log_action(f"modes/ft8 chase_start: spawned {' '.join(args)} ({desc})")
    return True, {"started": True, "rx_autostarted": rx_autostarted}


def chase_stop(dryrun=False):
    """Mirrors dashboard._action_chase_stop's body exactly."""
    if dryrun:
        dashboard.log_action(f"[DRYRUN] would stop chaser: pkill -f {dashboard.QSO_PY}")
        return {"stopped": True, "dryrun": True}
    ok = dashboard._pkill(dashboard.QSO_PY)
    dashboard.log_action(f"modes/ft8 chase_stop: pkill -f {dashboard.QSO_PY} -> {ok}")
    return {"stopped": ok}
