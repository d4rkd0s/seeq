"""Shared DXCC/country-prefix lookup — single source of truth for both the
dashboard's country display (bin/dashboard.py templates this same JSON into
its embedded JS, replacing __CALL_PREFIXES_JSON__) and qso.py's DX Mode
country filter. Longest-matching-prefix wins, list order doesn't matter.
Data: bin/dxcc_prefixes.json (best-effort, not exhaustive — see callers).
"""
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dxcc_prefixes.json")


def _load_prefixes(path=None):
    """[[prefix, country], ...] or [] on any I/O/parse error — never raises,
    so a missing/corrupt file just disables the DX filter (see is_dx_call)
    instead of crashing qso.py or dashboard.py."""
    try:
        with open(path or _PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


CALL_PREFIXES = _load_prefixes()


def country_for_call(call):
    """Longest-matching-prefix country lookup. '' when call is falsy or no
    prefix matches. Must stay behaviorally identical to dashboard.py's
    callCountry() JS (same JSON source; cross-checked in tools/test_dxcc.py)."""
    if not call:
        return ""
    base = call.split("/")[0].upper()
    best = None
    for pfx, country in CALL_PREFIXES:
        if base.startswith(pfx) and (best is None or len(pfx) > len(best[0])):
            best = (pfx, country)
    return best[1] if best else ""


def is_dx_call(call, home_call):
    """True only when `call` resolves to a DIFFERENT, KNOWN country than
    `home_call`. Fails CLOSED: if either side's country is unresolved
    (unmapped prefix), returns False — an incomplete table can never let a
    same-country (or unverifiable) station slip through DX Mode's filter as
    if it were confirmed DX."""
    home, theirs = country_for_call(home_call), country_for_call(call)
    return bool(home) and bool(theirs) and home != theirs


def logged_countries(calls):
    """{country_for_call(c) for c in calls}, dropping unresolved/unmapped
    calls -- an unmapped call must never poison the set with a false ''
    entry that could make some other candidate look "not new" by accident."""
    countries = set()
    for call in calls:
        country = country_for_call(call)
        if country:
            countries.add(country)
    return countries


def is_new_country(call, logged):
    """True only when `call` resolves to a KNOWN country not already in
    `logged`. Fails CLOSED like is_dx_call: an unresolved call is never
    claimed as a new country -- that would be a false celebration (or, for
    ranking, a false priority boost)."""
    country = country_for_call(call)
    return bool(country) and country not in logged
