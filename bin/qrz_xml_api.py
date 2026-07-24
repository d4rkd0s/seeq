"""QRZ XML (Interactive Callsign Data) API client — stdlib only, network I/O
isolated to _get().

Protocol (https://xmldata.qrz.com/xml/current/, requires a QRZ "XML Data"
subscription — the callsign-lookup/bio product, DISTINCT from the "XML
Logbook Data" subscription bin/qrz_api.py/bin/logsync.py already use for
ADIF sync. Auth is also different: Logbook uses a single static API key
(KEY=...); this API uses your actual QRZ.com username+password to obtain a
short-lived session key (Key=...), which is then passed as s=... on every
subsequent lookup. Official spec: qrz.com/docs/xml/current_spec.html

Two-step flow:
  1. login(username, password) -> session key (cache it — QRZ documents it
     staying valid for the rest of the day; only re-login on expiry/error)
  2. lookup(session_key, callsign) -> bio fields, including "image" (a
     fully-qualified photo URL) when the operator has one on file — absent
     entirely (not an error) for a station with no photo uploaded

Everything except _get() is pure and unit-tested (tools/test_qrz_xml.py).
Credentials are only ever passed through as arguments — this module never
reads or stores them. See bin/dashboard.py for where they live on disk.
"""
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET

API_URL = "https://xmldata.qrz.com/xml/current/"
USER_AGENT = "SeeQ/1.6 (+https://github.com/d4rkd0s/seeq)"


def _get(params, timeout=20):
    """The single network touchpoint: GET via curl. Returns (ok, text) —
    ok False means transport failure, text is the error; ok True means QRZ
    answered, text is the raw XML body (which may still carry a <Session>
    <Error> — that's the caller's to parse)."""
    qs = urllib.parse.urlencode(params)
    try:
        r = subprocess.run(["curl", "-s", "-S", "-A", USER_AGENT,
                            "--max-time", str(timeout), f"{API_URL}?{qs}"],
                           capture_output=True, text=True, timeout=timeout + 5)
    except FileNotFoundError:
        return False, "curl not found on this system"
    except subprocess.TimeoutExpired:
        return False, "request timed out"
    if r.returncode != 0:
        return False, f"curl exit {r.returncode}: {r.stderr.strip()}"
    return True, r.stdout


def parse_response(xml_text):
    """Raw QRZDatabase XML -> {"session": {...}, "callsign": {...}}. Missing
    sections become empty dicts; never raises — malformed/empty XML just
    yields an empty parse, treated by callers as "no data", not a crash."""
    session, callsign = {}, {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"session": session, "callsign": callsign}
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    sess_el = root.find(f"{ns}Session")
    if sess_el is not None:
        for child in sess_el:
            session[child.tag.split("}")[-1]] = (child.text or "").strip()
    call_el = root.find(f"{ns}Callsign")
    if call_el is not None:
        for child in call_el:
            callsign[child.tag.split("}")[-1]] = (child.text or "").strip()
    return {"session": session, "callsign": callsign}


def login(username, password, timeout=20):
    """Returns (ok, session_key_or_reason). ok is False on transport
    failure OR a QRZ-reported <Error> (e.g. bad credentials) — either way
    the second value is a human-readable reason, never a session key."""
    ok, text = _get({"username": username, "password": password, "agent": "cota1.0"}, timeout)
    if not ok:
        return False, text
    sess = parse_response(text)["session"]
    if "Error" in sess:
        return False, sess["Error"]
    key = sess.get("Key")
    if not key:
        return False, "no session key in response"
    return True, key


def lookup(session_key, callsign, timeout=20):
    """Returns (ok, fields_or_reason). fields is the parsed <Callsign>
    block (call/fname/name/country/image/... — whatever QRZ has on file; a
    station with no bio photo simply has no "image" key, not an error).
    ok False for a session error (expired — caller should login() again
    and retry once), "not found", or transport failure; callers that want
    to distinguish expiry from a bad callsign can check the reason text
    themselves, but aren't required to."""
    ok, text = _get({"s": session_key, "callsign": callsign}, timeout)
    if not ok:
        return False, text
    parsed = parse_response(text)
    sess = parsed["session"]
    if "Error" in sess:
        return False, sess["Error"]
    return True, parsed["callsign"]
