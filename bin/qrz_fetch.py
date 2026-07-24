#!/usr/bin/env python3
"""seeq QRZ logbook fetch — refresh data/qrz-logbook.json from QRZ.

Pages through the QRZ Logbook (MAX:250 per request, AFTERLOGID paging as
the API guide recommends) and writes the full record list to a local JSON
cache the dashboard's Logbook widget merges against the station ADIF.
RX-only in spirit: never touches the rig, only HTTPS to QRZ.

Run detached by the dashboard (/action/qrz/refresh) or by hand:
  python3 bin/qrz_fetch.py
Exit 0 on success (including an empty logbook), 1 on auth/transport errors.
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
import logsync                      # key path + station.conf plumbing
import qrz_api
import station_config

_C = station_config.load()
DATA = os.path.expanduser(_C.get("DATA", os.path.join(ROOT, "data")))
CACHE_PATH = os.path.join(DATA, "qrz-logbook.json")
PAGE_SIZE = 250


def write_cache(records, note=""):
    os.makedirs(DATA, exist_ok=True)
    obj = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "count": len(records), "note": note, "records": records}
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, CACHE_PATH)


def main():
    key = logsync.read_key()
    if not key:
        print(f"no QRZ API key at {logsync.KEY_PATH} — nothing fetched", file=sys.stderr)
        return 1
    records, after, pages = [], 0, 0
    MAX_PAGES = 40                   # hard stop: 40*250 = 10k records is far
    while True:                      # beyond this station; guards a paging
        pages += 1                   # server bug from looping forever
        if pages > MAX_PAGES:
            print(f"fetch: page cap ({MAX_PAGES}) hit — keeping previous cache",
                  file=sys.stderr)
            return 1
        option = f"TYPE:ADIF;MAX:{PAGE_SIZE};AFTERLOGID:{after}"
        result, reason, page = qrz_api.fetch(key, option)
        if result != "OK":
            if result == "FAIL" and not records and not page:
                # QRZ answers RESULT=FAIL for an empty logbook — cache the
                # emptiness. TRANSPORT (network/curl) and AUTH failures must
                # NOT take this branch: they'd clobber a good cache with an
                # empty one while reporting success.
                write_cache([], note=f"fetch returned {result}: {reason[:120]}")
                print(f"fetch: empty/none ({result}: {reason[:120]}) — cache written")
                return 0
            print(f"fetch failed ({result}: {reason[:120]}) — keeping previous cache",
                  file=sys.stderr)
            return 1
        records.extend(page)
        print(f"fetch: +{len(page)} record(s) (total {len(records)})")
        if len(page) < PAGE_SIZE:
            break
        ids = [int(r["app_qrzlog_logid"]) for r in page
               if str(r.get("app_qrzlog_logid", "")).isdigit()]
        if not ids or max(ids) + 1 <= after:
            break                    # can't page (no/stale logids); stop safely
        after = max(ids) + 1
    write_cache(records)
    print(f"fetch: {len(records)} record(s) cached -> {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
