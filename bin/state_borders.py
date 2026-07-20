"""State/province border outlines for the dashboard map -- embedded, no runtime network needed. Derived from Natural Earth 50m admin-1 (public domain, naturalearthdata.com) via tools/geo_src/convert_borders.py. NOTE: Natural Earth only maintains admin-1 detail for 9 countries even at this resolution (AU, BR, CA, CN, IN, ID, RU, ZA, US) -- not worldwide; see the conversion report. Each record: name, country (admin name it belongs to), iso_a2, postal (US 2-letter code where applicable), path (relative-delta SVG)."""
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state_borders.json")


def _load_states(path=None):
    """[...] or [] on any I/O/parse error -- same fail-open convention as dxcc.py's _load_prefixes()."""
    try:
        with open(path or _PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


STATES = _load_states()
