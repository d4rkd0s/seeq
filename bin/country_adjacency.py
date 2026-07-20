"""ISO2 -> sorted list of neighboring countries' ISO2 codes. Derived from github.com/geodatasource/country-borders (public domain) via tools/geo_src/convert_borders.py. Island nations with no land border correctly have an empty list, not a missing key."""
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "country_adjacency.json")


def _load_adjacency(path=None):
    """[...] or {} on any I/O/parse error -- same fail-open convention as dxcc.py's _load_prefixes()."""
    try:
        with open(path or _PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


ADJACENCY = _load_adjacency()
