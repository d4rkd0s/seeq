"""Merged logbook view: local ADIF records cross-matched against records
fetched from QRZ Logbook. Pure functions, no I/O — unit-tested in
tools/test_qrz.py; the dashboard feeds in data it reads itself.

Matching is deliberately tolerant on time (default ±30 min) because that's
how QSL confirmation works in practice — the two stations' logged times
rarely agree to the second, and QRZ's own confirmation matching allows
drift. Call+band must match exactly (case-insensitive); each remote record
is consumed by at most one local record, nearest-in-time first.
"""
import calendar
import time


def qso_epoch(fields):
    """Epoch seconds from an ADIF record's qso_date + time_on (HHMM or
    HHMMSS). None when either is missing/malformed."""
    date = (fields.get("qso_date") or "").strip()
    t = (fields.get("time_on") or "").strip()
    if len(date) != 8 or len(t) not in (4, 6):
        return None
    if len(t) == 4:
        t += "00"
    try:
        return calendar.timegm(time.strptime(date + t, "%Y%m%d%H%M%S"))
    except ValueError:
        return None


def _key(fields):
    return ((fields.get("call") or "").upper(),
            (fields.get("band") or "").lower())


# app_qrzlog_status: C=Confirmed is the ONLY confirmed value (per QRZ's own
# field docs; N=not confirmed, 2=confirmation requested, S=request seen,
# R=request rejected, A=reserved — all shades of "not yet").
_CONFIRMED = {"C"}


def _row(local):
    return {
        "call": (local.get("call") or "").upper(),
        "band": local.get("band") or "",
        "grid": local.get("gridsquare") or "",
        "date": local.get("qso_date") or "",
        "time": local.get("time_on") or "",
        "sent": local.get("rst_sent") or "",
        "rcvd": local.get("rst_rcvd") or "",
        "qrz": "not synced",
        "qrz_logid": None,
    }


def merge(local, remote, synced_through=0, tol_s=1800):
    """local/remote: lists of ADIF fields dicts (local ones may carry the
    "_end" byte offset from adif.records_from_bytes). synced_through: the
    logsync byte offset — locals ending at/before it are known-uploaded
    even when the QRZ fetch cache is empty or stale.

    Returns one display row per local record (same order), with "qrz" one
    of: "confirmed" (matched a QRZ record the other station confirmed),
    "uploaded" (matched unconfirmed, or known-synced by offset), or
    "not synced"."""
    rows = [_row(l) for l in local]

    # candidate (|dt|, local_idx, remote_idx) pairs, same call+band,
    # both timestamped, within tolerance — then greedy nearest-first
    # assignment so each remote record is used at most once.
    remote_used = [False] * len(remote)
    cands = []
    for li, l in enumerate(local):
        lt = qso_epoch(l)
        if lt is None:
            continue
        lk = _key(l)
        for ri, r in enumerate(remote):
            if _key(r) != lk:
                continue
            rt = qso_epoch(r)
            if rt is None:
                continue
            dt = abs(rt - lt)
            if dt <= tol_s:
                cands.append((dt, li, ri))
    cands.sort()
    local_matched = [False] * len(local)
    for dt, li, ri in cands:
        if local_matched[li] or remote_used[ri]:
            continue
        local_matched[li] = True
        remote_used[ri] = True
        r = remote[ri]
        status = (r.get("app_qrzlog_status") or "").upper()
        rows[li]["qrz"] = "confirmed" if status in _CONFIRMED else "uploaded"
        rows[li]["qrz_logid"] = r.get("app_qrzlog_logid")

    # offset fallback: known-uploaded even without fetch data
    for li, l in enumerate(local):
        if rows[li]["qrz"] == "not synced":
            end = l.get("_end")
            if isinstance(end, int) and 0 < end <= synced_through:
                rows[li]["qrz"] = "uploaded"
    return rows
