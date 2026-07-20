#!/usr/bin/env python3
"""Tests for the embedded map border/adjacency data (bin/country_borders.py,
bin/state_borders.py, bin/country_adjacency.py) -- generated once from
Natural Earth 50m + geodatasource/country-borders by
tools/geo_src/convert_borders.py, loaded the same fail-open way as
dxcc.py's _load_prefixes(). No radio hardware, no network. Run:
python3 tools/test_borders.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import country_borders
import state_borders
import country_adjacency


class TestLoadersFailOpen(unittest.TestCase):
    """Same convention as dxcc.py's _load_prefixes(): a missing/corrupt
    file must never raise, just disable the feature (empty result)."""

    def test_countries_loader_fails_open(self):
        self.assertEqual(country_borders._load_countries("/nonexistent/path.json"), [])

    def test_states_loader_fails_open(self):
        self.assertEqual(state_borders._load_states("/nonexistent/path.json"), [])

    def test_adjacency_loader_fails_open(self):
        self.assertEqual(country_adjacency._load_adjacency("/nonexistent/path.json"), {})


class TestCountryBordersData(unittest.TestCase):
    def test_nonempty_and_worldwide(self):
        self.assertGreater(len(country_borders.COUNTRIES), 200)

    def test_known_country_present_with_expected_fields(self):
        usa = [c for c in country_borders.COUNTRIES if c["name"] == "United States of America"]
        self.assertEqual(len(usa), 1)
        self.assertEqual(usa[0]["iso2"], "US")
        self.assertIsInstance(usa[0]["pop"], int)
        self.assertGreater(len(usa[0]["path"]), 0)

    def test_microstates_not_dropped(self):
        # regression: Douglas-Peucker simplification once collapsed tiny
        # real countries below the 3-point polygon minimum and silently
        # deleted them -- Vatican, Nauru, Tuvalu must survive conversion.
        names = {c["name"] for c in country_borders.COUNTRIES}
        for tiny in ("Vatican", "Nauru", "Tuvalu"):
            self.assertIn(tiny, names)

    def test_bbox_uses_primary_landmass_not_whole_multipolygon(self):
        # regression: unioning every ring's bbox (including outlying
        # territories) made USA/Russia's bbox span almost the entire
        # 1000-wide map -- neighborZoomBBox() would then "zoom to
        # neighbors" by showing nearly the whole world. Every country's
        # bbox width must stay well under the full map width.
        for c in country_borders.COUNTRIES:
            if not c.get("bbox") or c["name"] == "Antarctica":
                continue  # genuinely spans every longitude at the pole -- not the antimeridian bug
            width = c["bbox"][2] - c["bbox"][0]
            self.assertLess(width, 500, c["name"])

    def test_usa_bbox_is_conus_scale_not_world_spanning(self):
        usa = [c for c in country_borders.COUNTRIES if c["name"] == "United States of America"][0]
        x0, y0, x1, y1 = usa["bbox"]
        self.assertGreater(x0, 100)
        self.assertLess(x1, 400)


class TestStateBordersData(unittest.TestCase):
    def test_nonempty(self):
        self.assertGreater(len(state_borders.STATES), 200)

    def test_wisconsin_present(self):
        wi = [s for s in state_borders.STATES
              if s["country"] == "United States of America" and s.get("postal") == "WI"]
        self.assertEqual(len(wi), 1)
        self.assertEqual(wi[0]["name"], "Wisconsin")


class TestCountryAdjacency(unittest.TestCase):
    def test_known_land_neighbors(self):
        self.assertEqual(country_adjacency.ADJACENCY.get("FI"), ["NO", "RU", "SE"])
        self.assertIn("CA", country_adjacency.ADJACENCY.get("US", []))
        self.assertIn("MX", country_adjacency.ADJACENCY.get("US", []))

    def test_island_nation_has_explicit_empty_list_not_missing_key(self):
        # a missing key would be indistinguishable from "not a recognized
        # country"; an island nation's zero land borders must be explicit.
        self.assertIn("JP", country_adjacency.ADJACENCY)
        self.assertEqual(country_adjacency.ADJACENCY["JP"], [])

    def test_covers_most_known_countries(self):
        known_iso2 = {c["iso2"] for c in country_borders.COUNTRIES if c.get("iso2")}
        covered = known_iso2 & set(country_adjacency.ADJACENCY.keys())
        self.assertGreater(len(covered) / len(known_iso2), 0.9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
