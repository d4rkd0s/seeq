"""JS8 mode: RX/process lifecycle wrapper for bin/mode_switch.py.

Exposes the same five-function contract as bin/modes/ft8/pipeline.py
(start/stop/is_running/sanity_check/preflight), but the thing being managed is
different in kind. FT8's pipeline wraps SeeQ's own scripts, which talk to the
radio through `rigctl`. JS8's pipeline wraps a **third-party GUI application**
that owns the CAT port and the audio devices itself, and whose only control
surface is a TCP JSON socket. Consequences worth stating up front:

* "Is the mode running" means "is that GUI up *and* answering its API" -- a
  process that launched but whose API never came up is a failed start, not a
  running mode, because SeeQ would have no way to stop it safely.
* stop() asks for a transmitter halt *before* killing anything, mirroring the
  ordering in FT8's stop() where `rigctl T 0` fires first and unconditionally.
  Killing the only process that can unkey the radio while it might still be
  keyed would be exactly backwards.
* SeeQ runs JS8Call-improved under its own instance name (`--rig-name SeeQ`),
  so it reads and writes its own settings file and can be process-matched
  precisely. A hand-run JS8Call is a separate instance with separate settings,
  and stop() will not kill it.

Deliberately NOT configured from here: the `Rig` model and `PTTMethod` keys.
Those decide whether CAT is opened at all and *how* the radio gets keyed. Their
values haven't been verified against a live instance, and a wrong guess is a
TX-safety misconfiguration rather than a cosmetic one -- so rig and PTT setup
stay a one-time GUI step done with the control operator present. See
build_settings().
"""
import configparser
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.dirname(os.path.dirname(_HERE))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
import dashboard  # noqa: E402

sys.path.insert(0, _HERE)
import api  # noqa: E402
import vendor  # noqa: E402

# --rig-name value. Namespaces both the settings file ("JS8Call - SeeQ.ini")
# and the process command line, so SeeQ's instance is unambiguous.
RIG_NAME = "SeeQ"
APP_NAME = "JS8Call"

RX_CAPTURE_PY = os.path.join(_HERE, "rx_capture.py")

# pgrep -f / pkill -f pattern (extended regex). Matching bare "JS8Call" would
# also match a hand-run copy and stop() would kill the operator's own session.
PROC_PATTERN = f"JS8Call.*--rig-name {RIG_NAME}"

# The GUI takes a few seconds to boot before its API listener is up.
STARTUP_TIMEOUT_S = 45
STARTUP_POLL_S = 1.0


def tcp_port():
    """station.conf may override the API port; the default is the fork's own
    TCPServerPort default (2442 -- see api.py for why it isn't 2242)."""
    try:
        return int(dashboard._C.get("JS8_TCP_PORT", api.DEFAULT_PORT))
    except (TypeError, ValueError):
        return api.DEFAULT_PORT


def config_ini_path(config_home=None):
    """Where JS8Call-improved reads its settings from.

    MultiSettings.cpp's settings_path() is
    `QStandardPaths::writableLocation(ConfigLocation) + applicationName() + ".ini"`.
    On Linux ConfigLocation is ~/.config itself (AppConfigLocation would have
    been the ~/.config/JS8Call/ form), and --rig-name appends " - <name>" to
    applicationName -- so the file is ~/.config/"JS8Call - SeeQ.ini".
    """
    base = config_home or os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, f"{APP_NAME} - {RIG_NAME}.ini")


def build_settings(conf, tcp_port):
    """The ini keys SeeQ takes responsibility for. Pure; no I/O.

    Scope is deliberate. Enabling the API is required for SeeQ to talk to
    JS8Call at all and carries no TX risk. Station identity is harmless and
    saves retyping. Rig selection and PTT method are excluded on purpose --
    see the module docstring.
    """
    settings = {
        # Off by default in Configuration.cpp; nothing listens until this is on.
        "AcceptTCPRequests": "true",
        "TCPServerPort": str(tcp_port),
        # No part of SeeQ speaks UDP to JS8Call -- don't open a second listener.
        "AcceptUDPRequests": "false",
    }
    if conf.get("MYCALL"):
        settings["MyCall"] = conf["MYCALL"]
    if conf.get("MYGRID"):
        settings["MyGrid"] = conf["MYGRID"]
    return settings


def write_settings(path, updates, section="General"):
    """Merge `updates` into the ini, leaving everything else untouched.

    Qt writes un-grouped QSettings keys into [General], which is where
    Configuration.cpp's AcceptTCPRequests/TCPServerPort lookups land. The
    operator's audio, rig, macro and MultiSettings entries must survive this,
    so it's a read-modify-write rather than a rewrite -- and interpolation is
    off because Qt escapes some values with % sequences that would otherwise
    raise.
    """
    cp = configparser.RawConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # Qt keys are case-sensitive
    if os.path.exists(path):
        cp.read(path)
    if not cp.has_section(section):
        cp.add_section(section)
    for k, v in updates.items():
        cp.set(section, k, str(v))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        cp.write(f, space_around_delimiters=False)
    os.replace(tmp, path)
    return path


def is_running(running_fn=None):
    if running_fn:
        return running_fn()
    return dashboard._proc_running(PROC_PATTERN)


def _reachable(port=None):
    return api.is_reachable(port=port or tcp_port(), timeout=1.5)


def start(dryrun=False, *, ini_path=None, ensure_fn=None, spawn_fn=None,
          running_fn=None, reachable_fn=None, sleep_fn=time.sleep,
          clock_fn=time.monotonic, timeout_s=STARTUP_TIMEOUT_S):
    """Install-if-needed, configure, launch, and wait for the API to answer.

    Returns {"started": bool, ...}. A launch whose API never comes up returns
    started=False with an error, so mode_switch.py aborts the changeover rather
    than marking JS8 active with no way to control it.
    """
    ensure_fn = ensure_fn or (lambda: vendor.ensure_installed(log_fn=dashboard.log_action))
    spawn_fn = spawn_fn or dashboard._spawn_detached
    reachable_fn = reachable_fn or _reachable
    path = ini_path or config_ini_path()

    if dryrun:
        dashboard.log_action(
            f"[DRYRUN] would launch JS8Call-improved (--rig-name {RIG_NAME}), "
            f"enable its TCP API on port {tcp_port()}, and start rx_capture.py")
        return {"started": True, "dryrun": True}

    if is_running(running_fn):
        dashboard.log_action("modes/js8: JS8Call-improved already running, no-op")
        return {"started": False, "already": True}

    try:
        binary, source = ensure_fn()
    except vendor.Js8VendorError as e:
        dashboard.log_action(f"modes/js8 start: no binary available: {e}")
        return {"started": False, "error": str(e)}

    write_settings(path, build_settings(dashboard._C, tcp_port()))
    dashboard.log_action(f"modes/js8: wrote API settings to {path}")

    spawn_fn([binary, "--rig-name", RIG_NAME],
             os.path.join(dashboard.DATA, "js8call.log"))
    dashboard.log_action(f"modes/js8: launched {binary} (source: {source})")

    deadline = clock_fn() + timeout_s
    while True:
        if reachable_fn():
            break
        if clock_fn() >= deadline:
            dashboard.log_action(
                f"modes/js8 start: API never came up on port {tcp_port()} "
                f"within {timeout_s}s")
            return {"started": False,
                    "error": f"JS8Call API did not answer on port {tcp_port()} "
                             f"within {timeout_s}s"}
        sleep_fn(STARTUP_POLL_S)

    spawn_fn([sys.executable, RX_CAPTURE_PY],
             os.path.join(dashboard.DATA, "js8-capture.log"))
    dashboard.log_action("modes/js8: decode capture started")
    return {"started": True, "binary": binary, "source": source}


def stop(dryrun=False, *, client_fn=None, pkill_fn=None):
    """Halt the transmitter first, then shut the GUI and capture down.

    The halt is best-effort by necessity: if JS8Call-improved is already wedged
    or gone, nothing will answer. That must not abort the shutdown -- a failed
    halt is precisely the case where killing the process matters most.
    """
    if dryrun:
        dashboard.log_action(
            f"[DRYRUN] would stand down JS8: RIG.TX_HALT; pkill -f {PROC_PATTERN}; "
            f"pkill -f {RX_CAPTURE_PY}")
        return {"stopped": True, "dryrun": True}

    client_fn = client_fn or (lambda: api.Js8Client(port=tcp_port(), timeout=3.0))
    pkill_fn = pkill_fn or dashboard._pkill

    halted = False
    client = None
    try:
        client = client_fn()
        client.connect()
        client.tx_halt()
        halted = True
    except Exception as e:
        dashboard.log_action(f"modes/js8 stop: RIG.TX_HALT unavailable ({e!r}) -- "
                              f"continuing to kill anyway")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    killed_capture = pkill_fn(RX_CAPTURE_PY)
    ok = pkill_fn(PROC_PATTERN)
    dashboard.log_action(f"modes/js8 stop: TX_HALT sent -> {halted}; "
                          f"pkill -f {RX_CAPTURE_PY} -> {killed_capture}; "
                          f"pkill -f {PROC_PATTERN} -> {ok}")
    return {"stopped": True, "halted": halted, "capture_killed": killed_capture}


def sanity_check(running_fn=None, reachable_fn=None):
    """(ok, detail) -- confirms a mode we just told to stop actually did.

    Both checks matter: the process pattern can miss (the GUI re-execs itself
    out of a FUSE mount), and a still-answering API means something is still
    holding the CAT and audio devices, which is what the next mode is about to
    claim.
    """
    reachable_fn = reachable_fn or _reachable
    if is_running(running_fn):
        return False, "JS8Call-improved still running"
    if reachable_fn():
        return False, f"JS8Call API still answering on port {tcp_port()}"
    return True, "clear"


def preflight(find_fn=None):
    """(ok, detail) -- can this mode be started at all?

    Only checks that a verified binary exists; deliberately does not check
    whether the process is already running, matching FT8's preflight (start()
    is already a safe no-op in that case).
    """
    find_fn = find_fn or vendor.find_installed
    if find_fn() is None:
        return (False,
                f"JS8Call-improved {vendor.PINNED_VERSION} not installed and no "
                f"verified fallback found (run 'seeq doctor' for details)")
    return True, "clear"


def status():
    """Snapshot for the dashboard panel. Never raises."""
    port = tcp_port()
    running = is_running()
    reachable = api.is_reachable(port=port, timeout=1.0) if running else False
    return {"running": running, "api_reachable": reachable, "port": port,
            "instance": RIG_NAME, "version": vendor.PINNED_VERSION}


if __name__ == "__main__":
    import json
    print(json.dumps(status(), indent=2))
