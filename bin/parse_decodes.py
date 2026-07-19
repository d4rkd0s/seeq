#!/usr/bin/env python3
"""Parse jt9 stdout for one slot -> append to the hour-bucketed decode log,
rebuild status.json. Usage: parse_decodes.py YYMMDD HHMMSS < jt9out.txt
(run from data/). Decodes land in decodes/<YYYY-MM-DD>/<HH>.jsonl — see
decode_store.py; nothing reads the old flat decodes.jsonl anymore."""
import sys, re, json, time, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import station_config
import decode_store
import dxcc

_C = station_config.load()
DATE = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%y%m%d", time.gmtime())
SLOT = sys.argv[2] if len(sys.argv) > 2 else time.strftime("%H%M%S", time.gmtime())
ADIF = os.path.expanduser(_C.get("ADIF", "~/.local/share/WSJT-X/wsjtx_log.adi"))
MYCALL = _C.get("MYCALL", "N0CALL")

# jt9's own leading time column is a placeholder ("000000") for every decode
# in this pipeline — the raw slot.wav carries no real start-time metadata for
# jt9 to read it from — so it's matched (to split the line correctly) but not
# kept. DATE+SLOT (this wrapper's own real UTC clock read at capture time,
# 15 s resolution) is the trustworthy timestamp; see the "ts" field below.
line_re = re.compile(r"^\s*\d{4,6}\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+\S\s+(.+?)\s*$")
TS = decode_store.timestamp(DATE, SLOT)
decodes = []
for line in sys.stdin:
    m = line_re.match(line)
    if not m:
        continue
    snr, dt, freq, msg = m.groups()
    decodes.append({"date": DATE, "slot": SLOT, "ts": TS, "snr": int(snr),
                    "dt": float(dt), "freq": int(freq), "msg": msg})

decode_store.append(".", DATE, SLOT, decodes)

# recent decodes (last 60)
recent = []
for l in decode_store.tail(".", 60):
    try:
        recent.append(json.loads(l))
    except json.JSONDecodeError:
        pass

# worked calls from the WSJT-X ADIF log
worked, qsos = set(), []
try:
    adi = open(ADIF, errors="ignore").read()
    def fld(rec, name):
        m = re.search(rf"<{name}:(\d+)>", rec, re.I)
        return rec[m.end():m.end() + int(m.group(1))] if m else ""
    for rec in adi.split("<eor>"):
        call = fld(rec, "call")
        if call:
            worked.add(call.upper())
            qsos.append({"call": call.upper(), "band": fld(rec, "band"),
                         "date": fld(rec, "qso_date"), "grid": fld(rec, "gridsquare")})
except FileNotFoundError:
    pass

# candidate next calls: CQs in the last 3 slots, unworked, best SNR first.
# Modifier whitelist (DX/POTA/...) so 1x1 special-event calls like "CQ K2A FN13"
# aren't eaten by a greedy optional group (which made the grid parse as the call).
cq_re = re.compile(
    r"^CQ(?:\s+(?:DX|NA|EU|AS|SA|AF|OC|POTA|SOTA|QRP|TEST))?"
    r"\s+([A-Z0-9/]{3,})\s*([A-R]{2}[0-9]{2})?\b", re.I)
slots_seen = sorted({d["slot"] for d in recent})[-3:]
cands = {}
for d in recent:
    if d["slot"] not in slots_seen:
        continue
    m = cq_re.match(d["msg"])
    if m:
        call = m.group(1).upper()
        if call != MYCALL and call not in worked:
            if call not in cands or d["snr"] > cands[call]["snr"]:
                cands[call] = {"call": call, "grid": m.group(2) or "",
                               "snr": d["snr"], "freq": d["freq"], "slot": d["slot"]}
ranked = sorted(cands.values(), key=lambda c: -c["snr"])

# DX Mode's "new country" tag: country resolved via the shared prefix table,
# new_country true only when that country isn't anywhere in the all-time
# worked set above (fails closed on unmapped prefixes -- see dxcc.py).
logged = dxcc.logged_countries(worked)
for c in ranked:
    c["country"] = dxcc.country_for_call(c["call"])
    c["new_country"] = dxcc.is_new_country(c["call"], logged)

# anyone calling ME?
calling_me = [d for d in recent if d["slot"] in slots_seen
              and d["msg"].upper().startswith(MYCALL + " ")]

status = {
    "updated_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    "slot": SLOT, "slot_decodes": len(decodes),
    "recent": recent[-40:],
    "next_call": ranked[0] if ranked else None,
    "candidates": ranked[:8],
    "calling_me": calling_me,
    "qso_count": len(qsos),
    "qsos": qsos[-25:],
}
tmp = "status.json.tmp"
with open(tmp, "w") as f:
    json.dump(status, f)
os.replace(tmp, "status.json")
print(f"{SLOT}: {len(decodes)} decodes, next={status['next_call']['call'] if status['next_call'] else '-'}")
