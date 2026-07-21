#!/usr/bin/env python3
"""coa mode switch <name> -- the sequenced, polled mode-changeover state
machine (M0). Per Logan's explicit instruction, switching modes is a
deliberate operational changeover, not an instant swap: stop the current
mode fully, WAIT until it's actually confirmed stopped, sanity-check, only
then start the target mode. A failed sanity check always hard-aborts the
switch -- it never silently proceeds.

Usage:
  mode_switch.py switch <name>   run the changeover, print the result
  mode_switch.py status          print the current active mode + last switch status
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard  # noqa: E402 -- for atomic_write_json/DATA/log_action reuse
import mode_registry  # noqa: E402

STATUS_PATH = os.path.join(dashboard.DATA, "mode-switch.json")
ACTIVE_MODE_PATH = os.path.join(dashboard.DATA, "active-mode.json")

DEFAULT_POLL_TIMEOUT_S = 40  # within Logan's confirmed 30-45s deliberate-changeover window


def _read_active_mode(path):
    """Current active mode name, or None if unset/unreadable -- fail-open,
    same convention as every other embedded-state loader in this app."""
    try:
        with open(path) as f:
            return json.load(f).get("mode")
    except (OSError, ValueError):
        return None


def _write_active_mode(path, mode):
    dashboard.atomic_write_json(path, {"mode": mode, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def plan_changeover_stages(current_mode, target_mode):
    """Ordered stage-name list for a changeover. Pure function, no I/O.
    current_mode=None (boot / nothing active) naturally skips the
    stop/verify/sanity-of-current stages -- there's nothing to stop, not a
    special-cased fast path. Switching to the already-active mode
    short-circuits to a single no-op stage."""
    if current_mode is not None and current_mode == target_mode:
        return ["already_active"]
    stages = []
    if current_mode is not None:
        stages += ["stopping", "verifying", "sanity_check"]
    else:
        stages += ["sanity_check"]
    stages += ["starting", "done"]
    return stages


def run_changeover(target_mode, poll_timeout_s=DEFAULT_POLL_TIMEOUT_S, dryrun=False,
                    sleep_fn=time.sleep, clock_fn=time.time,
                    status_path=None, active_mode_path=None, load_mode_fn=None):
    """(ok, detail). Writes staged progress to status_path at every stage
    transition; writes active_mode_path only on full success. A failure at
    any stage writes stage="error" and returns False without ever touching
    active_mode_path -- the caller (dashboard/coa) must treat that as "the
    switch did not happen," not partial progress."""
    status_path = status_path or STATUS_PATH
    active_mode_path = active_mode_path or ACTIVE_MODE_PATH
    load_mode_fn = load_mode_fn or mode_registry.load_mode
    current_mode = _read_active_mode(active_mode_path)
    stages = plan_changeover_stages(current_mode, target_mode)

    def write(stage, **extra):
        dashboard.atomic_write_json(status_path, {
            "stage": stage, "from": current_mode, "to": target_mode,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **extra})

    if stages == ["already_active"]:
        write("already_active")
        return True, f"{target_mode} already active"

    try:
        target_pipeline, _ = load_mode_fn(target_mode)
    except mode_registry.UnknownModeError as e:
        write("error", detail=str(e))
        return False, str(e)

    if current_mode is not None:
        try:
            current_pipeline, _ = load_mode_fn(current_mode)
        except mode_registry.UnknownModeError as e:
            detail = f"current mode {current_mode!r} not registered: {e}"
            write("error", detail=detail)
            return False, detail

        write("stopping")
        current_pipeline.stop(dryrun=dryrun)

        write("verifying")
        deadline = clock_fn() + poll_timeout_s
        while current_pipeline.is_running():
            if clock_fn() >= deadline:
                detail = f"{current_mode} did not stop within {poll_timeout_s}s"
                write("error", detail=detail)
                return False, detail
            sleep_fn(1)

        write("sanity_check")
        ok, detail = current_pipeline.sanity_check()
        if not ok:
            write("error", detail=detail)
            return False, detail
    else:
        write("sanity_check")
        ok, detail = target_pipeline.preflight()
        if not ok:
            write("error", detail=detail)
            return False, detail

    write("starting")
    target_pipeline.start(dryrun=dryrun)

    write("done")
    if not dryrun:
        _write_active_mode(active_mode_path, target_mode)
    return True, f"{target_mode} active"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sw = sub.add_parser("switch")
    sw.add_argument("mode")
    sub.add_parser("status")
    args = ap.parse_args()

    if args.cmd == "status":
        status = None
        try:
            with open(STATUS_PATH) as f:
                status = json.load(f)
        except (OSError, ValueError):
            pass
        print(json.dumps({"active_mode": _read_active_mode(ACTIVE_MODE_PATH),
                           "switch_status": status}, indent=2))
        return 0

    dryrun = dashboard.DRYRUN
    ok, detail = run_changeover(args.mode, dryrun=dryrun)
    print(detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
