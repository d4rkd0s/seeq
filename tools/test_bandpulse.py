#!/usr/bin/env python3
"""Unit tests for bin/bandpulse.py -- the client for Logan's own bandpulse.net
public API (docs: bandpulse.net/docs), used to show a top-3-bands banner on
the dashboard. Network touchpoint is curl via subprocess, same convention as
bin/qrz_api.py's post() -- tests monkeypatch bandpulse._curl (the one
network-touching function) so nothing here hits the real network.
Run: python3 tools/test_bandpulse.py
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import bandpulse

SAMPLE = {
    "api_version": "v1",
    "attribution": "Data courtesy of NOAA SWPC and KC2G (prop.kc2g.com)",
    "cell": "AA00",
    "calculatedAt": "2026-07-23T01:30:00.000Z",
    "bands": [
        {"id": "160m", "name": "160 m", "state": "yellow", "label": "Holding", "score": 58},
        {"id": "40m", "name": "40 m", "state": "green", "label": "Open", "score": 88},
        {"id": "20m", "name": "20 m", "state": "green", "label": "Open", "score": 84},
        {"id": "10m", "name": "10 m", "state": "gray", "label": "Unknown", "score": 10},
        {"id": "30m", "name": "30 m", "state": "green", "label": "Open", "score": 88},
    ],
}


class TestTopBands(unittest.TestCase):
    def test_sorted_by_score_descending(self):
        top = bandpulse.top_bands(SAMPLE, n=3)
        self.assertEqual([b["id"] for b in top], ["40m", "30m", "20m"])

    def test_limits_to_n(self):
        self.assertEqual(len(bandpulse.top_bands(SAMPLE, n=3)), 3)
        self.assertEqual(len(bandpulse.top_bands(SAMPLE, n=2)), 2)

    def test_fields_trimmed_to_banner_needs(self):
        top = bandpulse.top_bands(SAMPLE, n=1)
        self.assertEqual(set(top[0]), {"id", "name", "state", "label", "score"})

    def test_empty_bands_returns_empty(self):
        self.assertEqual(bandpulse.top_bands({"bands": []}), [])


class TestFetchConditions(unittest.TestCase):
    def setUp(self):
        self._orig_curl = bandpulse._curl

    def tearDown(self):
        bandpulse._curl = self._orig_curl

    def test_grid_required(self):
        ok, err = bandpulse.fetch_conditions("")
        self.assertFalse(ok)
        self.assertEqual(err, "grid required")

    def test_transport_failure_surfaces_as_not_ok(self):
        bandpulse._curl = lambda url, timeout=10: (False, "curl exit 6: could not resolve host")
        ok, err = bandpulse.fetch_conditions("AA00")
        self.assertFalse(ok)
        self.assertIn("could not resolve host", err)

    def test_bad_json_surfaces_as_not_ok(self):
        bandpulse._curl = lambda url, timeout=10: (True, "<html>not json</html>")
        ok, err = bandpulse.fetch_conditions("AA00")
        self.assertFalse(ok)
        self.assertIn("JSON", err)

    def test_unexpected_shape_surfaces_as_not_ok(self):
        bandpulse._curl = lambda url, timeout=10: (True, '{"error":"rate limited"}')
        ok, err = bandpulse.fetch_conditions("AA00")
        self.assertFalse(ok)
        self.assertEqual(err, "rate limited")

    def test_good_response_parsed(self):
        import json
        bandpulse._curl = lambda url, timeout=10: (True, json.dumps(SAMPLE))
        ok, data = bandpulse.fetch_conditions("AA00")
        self.assertTrue(ok)
        self.assertEqual(data["cell"], "AA00")

    def test_grid_is_passed_through_to_url(self):
        seen = {}
        def fake_curl(url, timeout=10):
            seen["url"] = url
            return False, "stop here"
        bandpulse._curl = fake_curl
        bandpulse.fetch_conditions("FN31pr")
        self.assertIn("FN31pr", seen["url"])


class TestGetCachedOrFetch(unittest.TestCase):
    """Uses injected clock_fn/fetch_fn -- no real time.sleep, no real
    network -- and a unique fake grid per test so module-level cache state
    never leaks between tests."""

    def test_first_call_fetches(self):
        calls = []
        def fetch_fn(grid):
            calls.append(grid)
            return True, {"bands": []}
        ok, data = bandpulse.get_cached_or_fetch("GRIDA", clock_fn=lambda: 1000.0, fetch_fn=fetch_fn)
        self.assertTrue(ok)
        self.assertEqual(calls, ["GRIDA"])

    def test_second_call_within_ttl_uses_cache(self):
        calls = []
        def fetch_fn(grid):
            calls.append(grid)
            return True, {"bands": [], "n": len(calls)}
        t = {"now": 1000.0}
        clock_fn = lambda: t["now"]
        bandpulse.get_cached_or_fetch("GRIDB", clock_fn=clock_fn, fetch_fn=fetch_fn)
        t["now"] += 60  # well within CACHE_TTL_S
        ok, data = bandpulse.get_cached_or_fetch("GRIDB", clock_fn=clock_fn, fetch_fn=fetch_fn)
        self.assertTrue(ok)
        self.assertEqual(calls, ["GRIDB"])  # not called again
        self.assertEqual(data["n"], 1)

    def test_call_after_ttl_expiry_refetches(self):
        calls = []
        def fetch_fn(grid):
            calls.append(grid)
            return True, {"bands": [], "n": len(calls)}
        t = {"now": 1000.0}
        clock_fn = lambda: t["now"]
        bandpulse.get_cached_or_fetch("GRIDC", clock_fn=clock_fn, fetch_fn=fetch_fn)
        t["now"] += bandpulse.CACHE_TTL_S + 1
        ok, data = bandpulse.get_cached_or_fetch("GRIDC", clock_fn=clock_fn, fetch_fn=fetch_fn)
        self.assertEqual(calls, ["GRIDC", "GRIDC"])
        self.assertEqual(data["n"], 2)

    def test_failure_with_no_prior_good_data_is_not_ok(self):
        fetch_fn = lambda grid: (False, "curl exit 6: could not resolve host")
        ok, err = bandpulse.get_cached_or_fetch("GRIDD", clock_fn=lambda: 1000.0, fetch_fn=fetch_fn)
        self.assertFalse(ok)
        self.assertIn("could not resolve host", err)

    def test_failure_after_prior_good_data_serves_stale(self):
        t = {"now": 1000.0}
        clock_fn = lambda: t["now"]
        good = {"bands": [{"id": "20m"}]}
        calls = {"n": 0}
        def fetch_fn(grid):
            calls["n"] += 1
            if calls["n"] == 1:
                return True, good
            return False, "network down"
        bandpulse.get_cached_or_fetch("GRIDE", clock_fn=clock_fn, fetch_fn=fetch_fn)
        t["now"] += bandpulse.CACHE_TTL_S + 1  # force a refetch attempt
        ok, data = bandpulse.get_cached_or_fetch("GRIDE", clock_fn=clock_fn, fetch_fn=fetch_fn)
        self.assertTrue(ok)  # fail-soft: stale good data beats blanking the banner
        self.assertEqual(data, good)

    def test_repeated_failures_do_not_hammer_upstream_within_ttl(self):
        calls = []
        def fetch_fn(grid):
            calls.append(grid)
            return False, "network down"
        t = {"now": 1000.0}
        clock_fn = lambda: t["now"]
        bandpulse.get_cached_or_fetch("GRIDF", clock_fn=clock_fn, fetch_fn=fetch_fn)
        t["now"] += 5
        bandpulse.get_cached_or_fetch("GRIDF", clock_fn=clock_fn, fetch_fn=fetch_fn)
        self.assertEqual(len(calls), 1)  # second call served the cached failure, no refetch


if __name__ == "__main__":
    unittest.main(verbosity=2)
