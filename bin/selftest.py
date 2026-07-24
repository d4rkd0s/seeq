#!/usr/bin/env python3
"""seeq selftest — proves the offline decode chain with no radio, no audio
device, and no antenna: synthesize a reference FT8 frame (ft8code + numpy
GFSK synth via tools/ft8synth.py), decode it with jt9, and check the expected
message comes back.

Entirely offline: writes only to a temp directory (never data/), and never
touches CAT, PulseAudio, or PTT. Exit 0 on pass, 1 on fail.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

MESSAGE = "CQ N0CALL AA00"
OFFSET_HZ = 1500
# ft8code packs "CQ <call> <grid>" into the compact 28-bit-callsign format
# only for standard calls; N0CALL (6 chars) is nonstandard, so the encoded/
# decoded message loses the grid — expect the callsign part to round-trip.
EXPECTED_SUBSTR = "CQ N0CALL"

LINE_RE = re.compile(r"^\s*(\d{4,6})\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+\S\s+(.+?)\s*$")


def check_tool(name):
    if not shutil.which(name):
        print(f"FAIL missing required tool: {name}")
        return False
    return True


def main():
    ok = True
    for tool in ("ft8code", "jt9"):
        ok = check_tool(tool) and ok
    if not ok:
        print("selftest: cannot proceed without ft8code/jt9 (WSJT-X package) on PATH")
        return 1

    tmpdir = tempfile.mkdtemp(prefix="seeq-selftest-")
    try:
        wav = os.path.join(tmpdir, "ref.wav")
        synth = subprocess.run(
            [sys.executable, os.path.join(TOOLS, "ft8synth.py"), MESSAGE, wav, str(OFFSET_HZ)],
            capture_output=True, text=True, timeout=30)
        if synth.returncode != 0 or not os.path.exists(wav):
            print("FAIL ft8synth.py did not produce a WAV")
            print(synth.stdout)
            print(synth.stderr)
            return 1
        print(f"OK   synthesized reference frame: {synth.stdout.strip()}")

        jt9 = subprocess.run(["jt9", "-8", "-d", "2", wav],
                              capture_output=True, text=True, cwd=tmpdir, timeout=30)
        decoded_lines = []
        for line in jt9.stdout.splitlines():
            m = LINE_RE.match(line)
            if m:
                decoded_lines.append(m.group(5).strip())
        if not decoded_lines:
            print("FAIL jt9 produced no decodes at all")
            print("--- jt9 stdout ---")
            print(jt9.stdout)
            print("--- jt9 stderr ---")
            print(jt9.stderr)
            return 1

        print(f"OK   jt9 decoded {len(decoded_lines)} message(s): {', '.join(decoded_lines)}")
        if any(EXPECTED_SUBSTR in d.upper() for d in decoded_lines):
            print(f"PASS decode chain round-tripped '{EXPECTED_SUBSTR}' successfully")
            return 0
        print(f"FAIL expected a decode containing '{EXPECTED_SUBSTR}', got: {decoded_lines}")
        return 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
