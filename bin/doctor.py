#!/usr/bin/env python3
"""seeq doctor — non-interactive preflight diagnostics with fix-it hints.

Read-only: never opens the CAT serial port, never keys PTT, never starts
audio capture. Existence/presence checks only. Prints OK/WARN/FAIL + a
one-line remedy for each check; exits nonzero if any hard (FAIL) check fails.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
import station_config
import band_plan

RESULTS = []  # (status, name, detail, remedy)


def check(status, name, detail="", remedy=""):
    RESULTS.append((status, name, detail, remedy))


def run(cmd, timeout=8):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def check_ntp():
    r = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if r.returncode == 0 and r.stdout.strip() == "yes":
        check("OK", "clock NTP-synced")
    else:
        check("WARN", "clock NOT NTP-synced",
              remedy="FT8 needs <1s accuracy: sudo timedatectl set-ntp true")


def check_tools():
    tools = {
        "jt9": "install WSJT-X (apt: wsjtx / dnf: wsjtx)",
        "ft8code": "install WSJT-X (apt: wsjtx / dnf: wsjtx)",
        "rigctl": "install Hamlib (apt: libhamlib-utils / dnf: hamlib)",
        "sox": "install sox (apt/dnf: sox)",
        "parecord": "install PulseAudio utils (apt: pulseaudio-utils / dnf: pulseaudio-utils)",
        "pactl": "install PulseAudio utils (apt: pulseaudio-utils / dnf: pulseaudio-utils)",
    }
    for name, remedy in tools.items():
        if shutil.which(name):
            check("OK", f"{name} present")
        else:
            check("FAIL", f"{name} missing", remedy=remedy)


def check_python_numpy():
    check("OK", f"python3 {sys.version.split()[0]}")
    try:
        import numpy  # noqa: F401
        check("OK", f"numpy {numpy.__version__} importable")
    except ImportError:
        check("FAIL", "numpy not importable",
              remedy="pip install numpy, or apt/dnf install python3-numpy")


def check_station_conf():
    path = os.path.join(ROOT, "station.conf")
    if not os.path.exists(path):
        check("FAIL", "station.conf missing",
              remedy="cp station.conf.example station.conf, then edit it (or run: seeq setup)")
        return {}
    cfg = station_config.load(path)
    if not cfg:
        check("FAIL", "station.conf present but did not parse (empty or unreadable)",
              remedy="check file permissions / syntax; compare against station.conf.example")
        return {}
    check("OK", f"station.conf parses ({len(cfg)} keys)")
    placeholders = []
    if cfg.get("MYCALL", "N0CALL") == "N0CALL":
        placeholders.append("MYCALL")
    if cfg.get("MYGRID", "AA00") == "AA00":
        placeholders.append("MYGRID")
    if placeholders:
        check("WARN", f"station.conf has placeholder value(s): {', '.join(placeholders)}",
              remedy="edit station.conf (or run: seeq setup) with your real callsign/grid")
    else:
        check("OK", f"callsign/grid configured ({cfg.get('MYCALL')} {cfg.get('MYGRID')})")
    return cfg


def check_cat_port(cfg):
    port = cfg.get("CAT_PORT")
    if not port:
        check("WARN", "CAT_PORT not set in station.conf", remedy="run: seeq setup")
        return
    if os.path.exists(port):
        check("OK", f"CAT port exists ({port})")
    else:
        check("FAIL", f"CAT port missing ({port})",
              remedy="check USB cable/adapter is plugged in; ls /dev/serial/by-id/ and update station.conf")


def check_audio_source(cfg):
    source = cfg.get("PA_SOURCE")
    if not source:
        check("WARN", "PA_SOURCE not set in station.conf", remedy="run: seeq setup")
        return
    r = run(["pactl", "list", "short", "sources"])
    if r.returncode != 0:
        check("WARN", "could not query pactl sources",
              remedy="is PulseAudio/PipeWire running? try: pactl info")
        return
    short = source.split(".")[0]
    if any(short in line for line in r.stdout.splitlines()):
        check("OK", f"audio source present ({source})")
    else:
        check("FAIL", f"audio source not found ({source})",
              remedy="pactl list short sources — update PA_SOURCE in station.conf (or run: seeq setup)")


def check_fcc_compliance(cfg):
    """Strict Part 97 gate: callsign format, band/frequency privileges for
    LICENSE_CLASS, and power caps. Hard FAIL (not WARN) on any violation --
    same severity as a missing CAT port."""
    if not cfg:
        return
    ok, results = band_plan.verify(cfg)
    for r_ok, detail in results:
        check("OK" if r_ok else "FAIL", detail)


def check_disk_space():
    data_dir = os.path.join(ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    try:
        st = os.statvfs(data_dir)
        free_mb = st.f_bavail * st.f_frsize / (1024 * 1024)
    except OSError as e:
        check("WARN", "could not stat data/ for free space", remedy=str(e))
        return
    if free_mb < 200:
        check("FAIL", f"disk space low: {free_mb:.0f} MiB free under data/",
              remedy="free space — FT8 logs/waterfalls are small but a full disk stalls the RX loop")
    elif free_mb < 1024:
        check("WARN", f"disk space getting low: {free_mb:.0f} MiB free under data/",
              remedy="consider clearing old data/*.jsonl or waterfall PNGs")
    else:
        check("OK", f"disk space OK: {free_mb:.0f} MiB free under data/")


def main():
    print("=== SeeQ doctor ===")
    check_ntp()
    check_tools()
    check_python_numpy()
    cfg = check_station_conf()
    check_cat_port(cfg)
    check_audio_source(cfg)
    check_fcc_compliance(cfg)
    check_disk_space()

    hard_fail = False
    for status, name, detail, remedy in RESULTS:
        if status == "FAIL":
            hard_fail = True
        print(f"  {status:<4} {name}")
        if remedy:
            print(f"       -> {remedy}")

    n_ok = sum(1 for s, *_ in RESULTS if s == "OK")
    n_warn = sum(1 for s, *_ in RESULTS if s == "WARN")
    n_fail = sum(1 for s, *_ in RESULTS if s == "FAIL")
    print(f"\n{n_ok} OK, {n_warn} WARN, {n_fail} FAIL")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
