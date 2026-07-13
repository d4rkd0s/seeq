"""QRZ Logbook API client — stdlib only, network I/O isolated to post().

Protocol (https://logbook.qrz.com/api, requires an XML Logbook Data
subscription key; official guide: qrz.com/docs/logbook/QRZLogbookAPI.html):
form-POST with KEY + ACTION (+ ADIF/OPTION/LOGIDS), response is an
&-joined KEY=VALUE string, e.g.
    RESULT=OK&LOGIDS=130877825&COUNT=1
    STATUS=FAIL&RESULT=FAIL&REASON=Unable to add QSO to database: duplicate&EXTENDED=

Two wire quirks, verified against production implementations (Wavelog,
k0swe/qrz-logbook) as well as the official guide:
  * values are NOT URL-escaped — and FETCH's ADIF value contains raw '&'
    characters (angle brackets arrive HTML-entity-encoded: &lt; &gt;), so
    a naive &-split shreds it. parse_fields therefore only treats *known
    response keys* as field boundaries.
  * QRZ asks for an identifiable (non-generic) User-Agent; generic agents
    risk rate limiting.

Everything except post() is pure and unit-tested (tools/test_qrz.py). The
API key is only ever passed through as an argument — this module never
reads or stores it. See bin/logsync.py for where the key lives on disk.
"""
import html
import re
import subprocess
import urllib.parse

import adif

API_URL = "https://logbook.qrz.com/api"
USER_AGENT = "COTA/1.2 (+https://github.com/d4rkd0s/cota)"

# Every response key QRZ is known to emit (top-level or inside STATUS's
# nested DATA blob — its inner keys being listed here is what lets flat
# parsing recover them). Order matters only for LOGIDS vs LOGID.
_KNOWN_KEYS = ("RESULT", "STATUS", "REASON", "COUNT", "ADIF", "LOGIDS",
               "LOGID", "DATA", "EXTENDED", "ACTION", "CALLSIGN", "OWNER",
               "BOOKID", "BOOK_NAME", "CONFIRMED", "DXCC_COUNT",
               "START_DATE", "END_DATE")
_KEY_RE = re.compile(r"(?:^|&)(" + "|".join(_KNOWN_KEYS) + r")=", re.I)


def parse_fields(text):
    """Response body -> {KEY_UPPER: raw_value}. Values are taken verbatim
    (QRZ does not URL-escape them); a field's value runs until the next
    known key boundary, so raw '&' inside a value (the ADIF blob's HTML
    entities) can't split it."""
    fields = {}
    matches = list(_KEY_RE.finditer(text or ""))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fields[m.group(1).upper()] = text[m.end():end]
    return fields


def result_of(fields, raw=""):
    """(result, reason) from parsed response fields. Some QRZ responses use
    STATUS instead of RESULT (notably duplicate INSERTs); reason falls back
    to the raw response so substring checks (e.g. "duplicate") still work
    when no REASON field is present."""
    result = (fields.get("RESULT") or fields.get("STATUS") or "").upper()
    reason = fields.get("REASON", raw)
    return result, reason


def extract_adif_records(fields):
    """FETCH responses carry the matching logbook records as one ADIF blob
    in the ADIF field (URL-decoded by parse_fields; QRZ additionally HTML-
    entity-encodes the angle brackets, hence the unescape). Returns
    [fields_dict, ...] via adif.records_from_bytes."""
    blob = fields.get("ADIF", "")
    if not blob:
        return []
    blob = html.unescape(blob)
    return adif.records_from_bytes(blob.encode("utf-8", errors="replace"))


def post(form, timeout=30):
    """The single network touchpoint: form-POST via curl.
    Returns (ok, text) — ok False means transport failure, text is the
    error; ok True means QRZ answered, text is the raw response body
    (which may still be RESULT=FAIL — that's the caller's to parse)."""
    body = urllib.parse.urlencode(form)
    try:
        r = subprocess.run(["curl", "-s", "-S", "-A", USER_AGENT,
                            "--max-time", str(timeout),
                            "--data", body, API_URL],
                           capture_output=True, text=True, timeout=timeout + 5)
    except FileNotFoundError:
        return False, "curl not found on this system"
    except subprocess.TimeoutExpired:
        return False, "request timed out"
    if r.returncode != 0:
        return False, f"curl exit {r.returncode}: {r.stderr.strip()}"
    return True, r.stdout.strip()


def insert(key, adif_record_str):
    """INSERT one ADIF record. Returns (result, reason)."""
    ok, text = post({"KEY": key, "ACTION": "INSERT", "ADIF": adif_record_str})
    if not ok:
        return "FAIL", text
    return result_of(parse_fields(text), raw=text)


def fetch(key, option="TYPE:ADIF"):
    """FETCH logbook records. Returns (result, reason, records) where
    records is [fields_dict, ...] (empty on failure). result is "TRANSPORT"
    when the request never got an HTTP answer (curl/network failure) —
    deliberately distinct from QRZ answering RESULT=FAIL, which is also
    what an empty logbook returns; callers must not confuse the two.
    OPTION criteria join with semicolons, e.g. "TYPE:ADIF;MAX:250"."""
    ok, text = post({"KEY": key, "ACTION": "FETCH", "OPTION": option}, timeout=60)
    if not ok:
        return "TRANSPORT", text, []
    fields = parse_fields(text)
    result, reason = result_of(fields, raw=text)
    return result, reason, extract_adif_records(fields)


def status(key):
    """ACTION=STATUS: logbook summary (record counts, confirmed counts,
    book metadata). Returns (result, fields)."""
    ok, text = post({"KEY": key, "ACTION": "STATUS"})
    if not ok:
        return "FAIL", {"REASON": text}
    fields = parse_fields(text)
    result, _ = result_of(fields, raw=text)
    return result, fields
