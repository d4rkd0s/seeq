"""Country border outlines for the dashboard map -- embedded, no runtime network needed. Derived from Natural Earth 50m admin-0 (public domain, naturalearthdata.com) via tools/geo_src/convert_borders.py -- same 1000x500 equirectangular projection as bin/world_map.py's WORLD_PATH and dashboard.py's ll2xy(). Each record: name, admin (sovereign admin name), iso2/iso3, pop (POP_EST), pop_year, path (relative-delta SVG)."""
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "country_borders.json")


def _load_countries(path=None):
    """[...] or [] on any I/O/parse error -- same fail-open convention as dxcc.py's _load_prefixes()."""
    try:
        with open(path or _PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


COUNTRIES = _load_countries()
