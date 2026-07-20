#!/usr/bin/env python3
"""One-time converter: Natural Earth GeoJSON (admin-0 countries, admin-1
states/provinces) -> compact embedded SVG path data for the dashboard map.

Same projection as bin/world_map.py's WORLD_PATH and dashboard.py's ll2xy():
    x = (lon + 180) / 360 * 1000,   y = (90 - lat) / 180 * 500
Same relative-delta path style (M for the first point, l deltas after),
same 0.1px rounding. Unlike WORLD_PATH's land-fill simplification (which
drops sub-1.5px specks -- fine for visual clutter on a coastline), this
does NOT drop small features: a tiny country is still a real, often
prized DXCC entity, so nothing is thrown away by area. Point count is
reduced with Douglas-Peucker simplification instead, which trims
redundant vertices without changing the visible shape at this scale.

Pure stdlib (no shapely/pyshp available in this environment). Outputs two
importable Python modules: bin/country_borders.py and bin/state_borders.py.

Run: python3 tools/geo_src/convert_borders.py
"""
import json
import os

SRC = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(SRC), "..", "bin")
BIN = os.path.normpath(BIN)

MW, MH = 1000, 500


def project(lon, lat):
    return ((lon + 180) / 360 * MW, (90 - lat) / 180 * MH)


def perp_dist(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    t = max(0, min(1, t))
    px, py = x1 + t * dx, y1 + t * dy
    return ((x - px) ** 2 + (y - py) ** 2) ** 0.5


def douglas_peucker(points, tol):
    if len(points) < 3:
        return points
    dmax, idx = 0, 0
    for i in range(1, len(points) - 1):
        d = perp_dist(points[i], points[0], points[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > tol:
        left = douglas_peucker(points[:idx + 1], tol)
        right = douglas_peucker(points[idx:], tol)
        return left[:-1] + right
    return [points[0], points[-1]]


def ring_to_path(ring, tol=0.15, simplify_min_points=50):
    pts = [project(lon, lat) for lon, lat in ring]
    if len(pts) > 3 and pts[0] == pts[-1]:
        pts = pts[:-1]
    # Only simplify rings with enough points to actually benefit -- a fixed
    # pixel-distance tolerance collapses small-point-count rings (a tiny
    # real country like Vatican/Nauru, sub-pixel at world-map scale) down
    # below the 3-point polygon minimum, silently deleting them. That's
    # fine for coastline clutter but never acceptable for a real country.
    if len(pts) > simplify_min_points:
        pts = douglas_peucker(pts, tol)
    if len(pts) < 3:
        return ""
    def fmt(v):
        r = round(v, 1)
        return str(int(r)) if r == int(r) else str(r)
    out = [f"M{fmt(pts[0][0])} {fmt(pts[0][1])}"]
    px, py = pts[0]
    for x, y in pts[1:]:
        dx, dy = round(x - px, 1), round(y - py, 1)
        if dx == 0 and dy == 0:
            continue
        out.append(f"l{fmt(dx)} {fmt(dy)}")
        px, py = px + dx, py + dy
    out.append("Z")
    return "".join(out)


def geom_to_path(geom, tol=0.15):
    t = geom["type"]
    polys = geom["coordinates"] if t == "MultiPolygon" else [geom["coordinates"]]
    parts = []
    for poly in polys:
        for ring in poly:
            p = ring_to_path(ring, tol)
            if p:
                parts.append(p)
    return "".join(parts)


def ring_area(pts):
    """Shoelace formula, absolute value -- used only to compare ring sizes
    (pick the primary landmass), not for any geographic/area purpose."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def primary_bbox(geom):
    """Bounding box of a feature's LARGEST ring by area, not the union of
    all its rings. A handful of countries (USA, Russia, France, etc.) have
    outlying territories that straddle the antimeridian or sit far from the
    mainland -- unioning every ring would blow the bbox out to nearly the
    whole map width for those. Using only the primary landmass keeps the
    neighbor auto-zoom (bin/dashboard.py's neighborZoomBBox) sane for every
    country, common case and edge case alike."""
    t = geom["type"]
    polys = geom["coordinates"] if t == "MultiPolygon" else [geom["coordinates"]]
    best_area, best_pts = -1, None
    for poly in polys:
        for ring in poly:
            pts = [project(lon, lat) for lon, lat in ring]
            a = ring_area(pts)
            if a > best_area:
                best_area, best_pts = a, pts
    if not best_pts:
        return None
    xs = [p[0] for p in best_pts]
    ys = [p[1] for p in best_pts]
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


def convert_countries():
    with open(os.path.join(SRC, "ne_50m_admin_0_countries.geojson")) as f:
        data = json.load(f)
    out = []
    for feat in data["features"]:
        p = feat["properties"]
        path = geom_to_path(feat["geometry"])
        if not path:
            continue
        out.append({
            "name": p.get("NAME") or p.get("ADMIN"),
            "admin": p.get("ADMIN"),
            "iso2": p.get("ISO_A2") if p.get("ISO_A2") not in (None, "-99") else p.get("ISO_A2_EH"),
            "iso3": p.get("ISO_A3") if p.get("ISO_A3") not in (None, "-99") else p.get("ISO_A3_EH"),
            "pop": p.get("POP_EST"),
            "pop_year": p.get("POP_YEAR"),
            "path": path,
            "bbox": primary_bbox(feat["geometry"]),
        })
    return out


def convert_states():
    with open(os.path.join(SRC, "ne_50m_admin_1_states_provinces.geojson")) as f:
        data = json.load(f)
    out = []
    for feat in data["features"]:
        p = feat["properties"]
        path = geom_to_path(feat["geometry"])
        if not path:
            continue
        out.append({
            "name": p.get("name"),
            "country": p.get("admin"),
            "iso_a2": p.get("iso_a2"),
            "postal": p.get("postal"),
            "path": path,
        })
    return out


def convert_adjacency(known_iso2):
    """ISO2 -> sorted list of neighboring ISO2 codes, from the GeoDataSource
    country-borders CSV (github.com/geodatasource/country-borders, public
    domain). `known_iso2` (every real ISO2 code from country_borders.json)
    is backfilled with an explicit empty list for any code that never
    appears in the CSV -- an island nation genuinely has zero land borders,
    which must be distinguishable from "not a recognized country" (a
    missing dict key), not silently absent either way."""
    import csv
    adjacency = {}
    with open(os.path.join(SRC, "country-borders.csv"), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a, b = row["country_code"], row["country_border_code"]
            if not a or not b:
                continue
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)
    for code in known_iso2:
        adjacency.setdefault(code, set())
    return {k: sorted(v) for k, v in sorted(adjacency.items())}


def write_json(path, records):
    with open(path, "w") as f:
        json.dump(records, f, separators=(",", ":"))


def write_loader(path, json_filename, varname, loader_fn, docstring, empty="[]"):
    with open(path, "w") as f:
        f.write(f'"""{docstring}"""\n')
        f.write('import json\nimport os\n\n')
        f.write('_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '
                 f'"{json_filename}")\n\n\n')
        f.write(f'def {loader_fn}(path=None):\n')
        f.write(f'    """[...] or {empty} on any I/O/parse error -- same fail-open '
                 f'convention as dxcc.py\'s _load_prefixes()."""\n')
        f.write('    try:\n        with open(path or _PATH) as fh:\n            return json.load(fh)\n')
        f.write(f'    except (OSError, ValueError):\n        return {empty}\n\n\n')
        f.write(f'{varname} = {loader_fn}()\n')


if __name__ == "__main__":
    countries = convert_countries()
    states = convert_states()
    write_json(os.path.join(BIN, "country_borders.json"), countries)
    write_json(os.path.join(BIN, "state_borders.json"), states)
    write_loader(
        os.path.join(BIN, "country_borders.py"), "country_borders.json",
        "COUNTRIES", "_load_countries",
        "Country border outlines for the dashboard map -- embedded, no runtime "
        "network needed. Derived from Natural Earth 50m admin-0 (public domain, "
        "naturalearthdata.com) via tools/geo_src/convert_borders.py -- same "
        "1000x500 equirectangular projection as bin/world_map.py's WORLD_PATH "
        "and dashboard.py's ll2xy(). Each record: name, admin (sovereign admin "
        "name), iso2/iso3, pop (POP_EST), pop_year, path (relative-delta SVG).")
    write_loader(
        os.path.join(BIN, "state_borders.py"), "state_borders.json",
        "STATES", "_load_states",
        "State/province border outlines for the dashboard map -- embedded, no "
        "runtime network needed. Derived from Natural Earth 50m admin-1 (public "
        "domain, naturalearthdata.com) via tools/geo_src/convert_borders.py. "
        "NOTE: Natural Earth only maintains admin-1 detail for 9 countries even "
        "at this resolution (AU, BR, CA, CN, IN, ID, RU, ZA, US) -- not "
        "worldwide; see the conversion report. Each record: name, country "
        "(admin name it belongs to), iso_a2, postal (US 2-letter code where "
        "applicable), path (relative-delta SVG).")
    known_iso2 = {c["iso2"] for c in countries if c.get("iso2") and c["iso2"] != "-99"}
    adjacency = convert_adjacency(known_iso2)
    write_json(os.path.join(BIN, "country_adjacency.json"), adjacency)
    write_loader(
        os.path.join(BIN, "country_adjacency.py"), "country_adjacency.json",
        "ADJACENCY", "_load_adjacency",
        "ISO2 -> sorted list of neighboring countries' ISO2 codes. Derived "
        "from github.com/geodatasource/country-borders (public domain) via "
        "tools/geo_src/convert_borders.py. Island nations with no land "
        "border correctly have an empty list, not a missing key.",
        empty="{}")
    print(f"countries: {len(countries)} features")
    print(f"states: {len(states)} features")
    print(f"adjacency: {len(adjacency)} countries with border data")
    print(f"country_borders.json: {os.path.getsize(os.path.join(BIN, 'country_borders.json'))/1024:.1f} KB")
    print(f"state_borders.json: {os.path.getsize(os.path.join(BIN, 'state_borders.json'))/1024:.1f} KB")
    print(f"country_adjacency.json: {os.path.getsize(os.path.join(BIN, 'country_adjacency.json'))/1024:.1f} KB")
