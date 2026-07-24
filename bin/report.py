#!/usr/bin/env python3
"""seeq report — compact, read-only session report.

Sources (all optional; missing files just mean zero counts, never an error):
  data/qso-attempts.jsonl   one line per LOGGED QSO (from bin/qso.py log_qso)
  data/attempts.jsonl       one line per CQ chased, logged or not (outcome field)
  <ADIF path from station.conf>   authoritative QSO records (band, date, call)

Prints: QSOs today / total, attempts today, per-band counts, last QSO line.
Never writes anything.
"""
import collections
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
import station_config

_C = station_config.load()
DATA = os.path.expanduser(_C.get("DATA", os.path.join(ROOT, "data")))
ADIF = os.path.expanduser(_C.get("ADIF", "~/.local/share/WSJT-X/wsjtx_log.adi"))
QSO_LOG = os.path.join(DATA, "qso-attempts.jsonl")
ATTEMPTS_LOG = os.path.join(DATA, "attempts.jsonl")


def read_jsonl(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return out


def parse_adif(path):
    """Return list of {call, band, qso_date} from a standard ADIF export."""
    recs = []
    try:
        text = open(path, errors="ignore").read()
    except FileNotFoundError:
        return recs

    def fld(rec, name):
        m = re.search(rf"<{name}:(\d+)>", rec, re.I)
        return rec[m.end():m.end() + int(m.group(1))] if m else ""

    for rec in text.split("<eor>"):
        call = fld(rec, "call")
        if call:
            recs.append({"call": call.upper(), "band": fld(rec, "band"),
                         "qso_date": fld(rec, "qso_date")})
    return recs


def main():
    today_adif = time.strftime("%Y%m%d", time.gmtime())
    today_dash = time.strftime("%Y-%m-%d", time.gmtime())

    qsos = parse_adif(ADIF)
    total_qsos = len(qsos)
    today_qsos = [q for q in qsos if q["qso_date"] == today_adif]
    per_band = collections.Counter(q["band"] for q in qsos if q["band"])

    attempts = read_jsonl(ATTEMPTS_LOG)
    attempts_today = [a for a in attempts if str(a.get("utc", "")).startswith(today_dash)]
    outcomes_today = collections.Counter(a.get("outcome", "?") for a in attempts_today)

    logged = read_jsonl(QSO_LOG)
    last = logged[-1] if logged else None

    print("=== SeeQ session report ===")
    print(f"generated {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}Z")
    print()
    if not qsos and not attempts and not logged:
        print("no activity recorded yet (data/*.jsonl and the ADIF are all empty or missing)")
        return 0

    print(f"QSOs today:   {len(today_qsos)}")
    print(f"QSOs total:   {total_qsos}")
    print(f"attempts today: {len(attempts_today)}"
          + (f"  ({', '.join(f'{k}={v}' for k, v in sorted(outcomes_today.items()))})"
             if outcomes_today else ""))
    print()
    if per_band:
        print("per-band (all-time):")
        for band, n in sorted(per_band.items(), key=lambda kv: -kv[1]):
            print(f"  {band:<6} {n}")
    else:
        print("per-band: (no banded QSOs in ADIF yet)")
    print()
    if last:
        print(f"last QSO: {last.get('date','?')} {last.get('utc','?')}Z  "
              f"{last.get('call','?'):<10} {last.get('grid') or '----':<6} "
              f"sent {last.get('sent','?')}  rcvd {last.get('rcvd','?')}")
    else:
        print("last QSO: (none logged yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
