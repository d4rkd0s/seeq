#!/usr/bin/env python3
"""seeq setup — interactive first-run wizard: detect hardware, write station.conf.

READ-ONLY detection only (pactl/arecord/ls/rigctl -l never open the radio or a
port). The only place this script can touch the rig is the final, opt-in
"test CAT now?" step, and only if the user explicitly answers yes — this
script never keys PTT and never runs qso.py.

Usage: seeq setup   (or: python3 bin/setup.py [--output PATH] [--example PATH])
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CALL_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z]{1,4}(?:/[A-Z0-9]{1,4})?$")
GRID4_RE = re.compile(r"^[A-R]{2}[0-9]{2}$", re.I)
GRID6_RE = re.compile(r"^[A-R]{2}[0-9]{2}[A-X]{2}$", re.I)

BAND_PRESETS = {
    "160m": 1840000, "80m": 3573000, "60m": 5357000, "40m": 7074000,
    "30m": 10136000, "20m": 14074000, "17m": 18100000, "15m": 21074000,
    "12m": 24915000, "10m": 28074000, "6m": 50313000, "2m": 144174000,
}


def ask(prompt, default=None):
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        try:
            v = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            print()
            raise SystemExit("aborted: no more input (setup wizard needs an interactive terminal)")
        if v:
            return v
        if default is not None:
            return default
        print("  a value is required.")


def ask_yn(prompt, default=False):
    d = "Y/n" if default else "y/N"
    try:
        v = input(f"{prompt} [{d}]: ").strip().lower()
    except EOFError:
        print()
        return default
    if not v:
        return default
    return v.startswith("y")


def run(cmd):
    """Run a read-only detection command; never raises, returns stdout or ''."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pick_from_list(items, label, allow_manual=True):
    """Print a numbered menu, return the chosen item (or None if the user
    types a manual value when allow_manual, or '' if the list was empty)."""
    if not items:
        print(f"  (no {label} detected)")
        return None
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it}")
    while True:
        raw = ask(f"pick {label} by number" + (" (or type a value manually)" if allow_manual else ""),
                   default="1" if len(items) == 1 else None)
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        if allow_manual and raw:
            return raw
        print("  invalid choice.")


def detect_audio_sources():
    out = run(["pactl", "list", "short", "sources"])
    sources = []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) >= 2 and "monitor" not in cols[1]:
            sources.append(cols[1])
    return sources


def detect_serial_ports():
    d = "/dev/serial/by-id"
    try:
        return sorted(os.listdir(d))
    except OSError:
        return []


def detect_alsa_cards():
    out = run(["arecord", "-l"])
    cards = []
    for m in re.finditer(r"^card \d+: (\S+) \[([^\]]+)\]", out, re.M):
        cards.append(m.group(1))
    return cards


def rigctl_list():
    out = run(["rigctl", "-l"])
    rigs = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\S+)\s+(.+?)\s{2,}", line)
        if m:
            rigs.append((m.group(1), m.group(2), m.group(3).strip()))
    return rigs


def pick_rig():
    rigs = rigctl_list()
    if not rigs:
        print("  rigctl -l gave no output — is Hamlib installed? Enter the model number manually.")
        return ask("Hamlib rig model number", "3060")
    print(f"  {len(rigs)} rigs known to this Hamlib build.")
    while True:
        term = ask("search rig by name (e.g. 'xiegu', 'icom', 'yaesu'; blank to list all)", "")
        matches = [r for r in rigs if term.lower() in r[1].lower() or term.lower() in r[2].lower()] if term else rigs
        if not matches:
            print("  no matches, try again.")
            continue
        shown = matches[:30]
        for mid, mfg, model in shown:
            print(f"  {mid:>6}  {mfg:<12} {model}")
        if len(matches) > len(shown):
            print(f"  ...and {len(matches) - len(shown)} more; narrow your search.")
        choice = ask("model number (or blank to search again)", "")
        if not choice:
            continue
        if any(choice == r[0] for r in rigs):
            return choice
        print("  not a model number from the list above; try again.")


def validate_callsign(c):
    return bool(CALL_RE.match(c.upper()))


def validate_grid(g):
    return bool(GRID4_RE.match(g) or GRID6_RE.match(g))


def ask_callsign():
    while True:
        c = ask("your callsign", None).upper()
        if validate_callsign(c):
            return c
        print("  doesn't look like a callsign (e.g. W1AW, N0CALL) — try again.")


def ask_grid():
    while True:
        g = ask("your grid square (4 or 6 chars, e.g. FN20 or FN20ab)", None)
        if validate_grid(g):
            return (g[:2].upper() + g[2:4] + (g[4:6].lower() if len(g) >= 6 else ""))
        print("  not a valid Maidenhead locator (2 letters, 2 digits, optional 2 letters) — try again.")


def ask_band():
    print("  common bands: " + ", ".join(sorted(BAND_PRESETS, key=lambda b: BAND_PRESETS[b])))
    while True:
        b = ask("band", "40m").lower()
        if b in BAND_PRESETS:
            return b, BAND_PRESETS[b]
        if b.isdigit():
            return f"{int(b)/1000000:g}Hz", int(b)
        print("  unknown band; enter one from the list above or a raw Hz value.")


def load_example(path):
    with open(path) as f:
        return f.readlines()


def apply_values(lines, values):
    """Rewrite KEY=... lines in-place, preserving comments/blank lines/order.
    Keys not present in `values` are left untouched (comment stays attached)."""
    out = []
    seen = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                # preserve any trailing inline comment
                comment = ""
                if "#" in line:
                    comment = "  #" + line.split("#", 1)[1].rstrip("\n")
                out.append(f"{key}={values[key]}{comment}\n")
                seen.add(key)
                continue
        out.append(line)
    missing = [k for k in values if k not in seen]
    if missing:
        out.append("\n# added by seeq setup\n")
        for k in missing:
            out.append(f"{k}={values[k]}\n")
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=os.path.join(ROOT, "station.conf"))
    ap.add_argument("--example", default=os.path.join(ROOT, "station.conf.example"))
    args = ap.parse_args()

    print("=== SeeQ setup wizard ===")
    print("Detection only (pactl/arecord/ls/rigctl -l) — nothing here touches the radio.")
    print()

    print("-- audio capture device --")
    sources = detect_audio_sources()
    pa_source = pick_from_list(sources, "audio source (pactl)")
    cards = detect_alsa_cards()
    alsa_card = pick_from_list(cards, "ALSA card name (arecord -l, for mixer calibration)") if cards else ask("ALSA card name", "Device")

    print()
    print("-- CAT serial port --")
    ports = detect_serial_ports()
    port_choice = pick_from_list(ports, "serial port (/dev/serial/by-id)")
    cat_port = f"/dev/serial/by-id/{port_choice}" if port_choice and not port_choice.startswith("/dev") else port_choice

    print()
    print("-- rig (Hamlib model) --")
    rig_model = pick_rig()
    cat_baud = ask("CAT baud rate", "19200")

    print()
    print("-- operator --")
    mycall = ask_callsign()
    mygrid = ask_grid()
    band, dial_hz = ask_band()
    power = ask("TX power (watts)", "5")

    values = {
        "MYCALL": mycall,
        "MYGRID": mygrid,
        "DIAL_HZ": dial_hz,
        "BAND": band,
        "TX_PWR": power,
        "CAT_PORT": cat_port or "/dev/serial/by-id/CHANGE_ME",
        "RIG_MODEL": rig_model,
        "CAT_BAUD": cat_baud,
        "ALSA_CARD": alsa_card or "Device",
    }
    if pa_source:
        values["PA_SOURCE"] = pa_source
        # best-effort matching sink from the same USB device name
        base = pa_source.split(".")[0] if "." in pa_source else pa_source
        for s in run(["pactl", "list", "short", "sinks"]).splitlines():
            cols = s.split("\t")
            if len(cols) >= 2 and base.split("-")[0] in cols[1] and "monitor" not in cols[1]:
                values["PA_SINK"] = cols[1]
                break

    lines = load_example(args.example)
    out_lines = apply_values(lines, values)
    with open(args.output, "w") as f:
        f.writelines(out_lines)
    print()
    print(f"wrote {args.output}")
    print("review MIXER_MIC / MIXER_SPEAKER levels once you've connected the rig — see docs/INSTALL.md.")

    print()
    if ask_yn("test CAT now? (runs one 'rigctl f' frequency read against the real port)", False):
        rig_cmd = ["rigctl", "-m", rig_model, "-r", cat_port or "", "-s", cat_baud, "f"]
        print(f"  running: {' '.join(rig_cmd)}")
        try:
            r = subprocess.run(rig_cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                print(f"  OK — radio reports dial frequency: {r.stdout.strip()} Hz")
            else:
                print(f"  no answer (rc={r.returncode}): {r.stderr.strip() or '(no output)'}")
                print("  check: radio powered on, CAT enabled, cable seated, RIG_MODEL/CAT_BAUD correct.")
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"  rigctl failed to run: {e}")
    else:
        print("  skipped — run 'seeq doctor' any time to re-check without touching the radio.")

    print()
    print("next: seeq selftest   (offline decode chain sanity check)")
    print("      seeq doctor     (full preflight diagnostics)")
    print("      seeq start      (receive-only)")


if __name__ == "__main__":
    main()
