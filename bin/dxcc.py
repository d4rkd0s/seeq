"""Shared DXCC/country-prefix lookup — single source of truth for both the
dashboard's country display (bin/dashboard.py templates this same JSON into
its embedded JS, replacing __CALL_PREFIXES_JSON__) and qso.py's DX Mode
country filter. Longest-matching-prefix wins, list order doesn't matter.
Data: bin/dxcc_prefixes.json (best-effort, not exhaustive — see callers).
"""
import json
import os

import country_adjacency
import country_borders

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dxcc_prefixes.json")

# Natural Earth's political-country names (country_borders.py, used for the
# map) mostly match dxcc.py's DXCC entity names verbatim (e.g. "Germany" ==
# "Germany") -- this alias table only covers the places they diverge.
_DXCC_TO_BORDER_NAME = {"United States": "United States of America"}


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


def dx_skip_reason(call, home_call):
    """Classify why `call` failed the DX filter, for LOGGING only --
    is_dx_call() above remains the actual gate, unchanged. 'unknown' means
    at least one side's country couldn't be resolved from the prefix table:
    a real gap in dxcc_prefixes.json worth closing. 'same' means both sides
    resolved fine but happen to share a country: correctly filtered, not a
    data gap. Only meaningful to call when is_dx_call() has already
    returned False for this pair."""
    home, theirs = country_for_call(home_call), country_for_call(call)
    if not home or not theirs:
        return "unknown"
    return "same"


def _iso2_for_country(name):
    """DXCC entity name -> ISO2, via country_borders.py's Natural Earth
    list (name or admin field). None when unresolvable -- some DXCC
    entities (Alaska, Hawaii, Puerto Rico...) are counted separately from
    their sovereign country and have no single matching political-country
    record; never guess in that case."""
    if not name:
        return None
    target = _DXCC_TO_BORDER_NAME.get(name, name)
    for c in country_borders.COUNTRIES:
        if c["name"] == target or c.get("admin") == target:
            return c.get("iso2")
    return None


def is_neighbor_call(call, home_call):
    """True only when `call`'s DXCC country shares a land border with
    `home_call`'s (bin/country_adjacency.json, geodatasource/country-
    borders) -- used to rank genuine long-distance DX above "easy"
    neighboring-country DX (e.g. Canada/Mexico for a US station). Fails
    CLOSED like is_dx_call/is_new_country: an unmapped call, an unmapped
    home_call, or a DXCC entity with no resolvable political-country ISO2
    is never claimed as a neighbor."""
    home_iso2 = _iso2_for_country(country_for_call(home_call))
    their_iso2 = _iso2_for_country(country_for_call(call))
    if not home_iso2 or not their_iso2:
        return False
    return their_iso2 in country_adjacency.ADJACENCY.get(home_iso2, [])


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
