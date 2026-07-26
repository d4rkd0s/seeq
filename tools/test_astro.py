#!/usr/bin/env python3
"""Tests for bin/astro.py -- sun/moon ephemeris for the map's day/night
terminator toggle and moon-position widget (EME situational awareness).

No live network/reference ephemeris is available in this environment, so
these are self-consistency and known-astronomy-fact tests rather than
bit-for-bit comparisons against an external almanac: solstice/equinox
declination sign and rough magnitude (well-known facts, generous
tolerance), mean-motion period cross-checks (the model's rate constants
must reproduce the textbook synodic/anomalistic month lengths), and
bounded-range sanity checks (declination/illumination can't exceed known
physical limits). See bin/astro.py's module docstring for the accuracy
tier this buys (~1-2 degrees for the Moon, better for the Sun) and why.

Run: python3 tools/test_astro.py
"""
import datetime
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASTRO = os.path.join(ROOT, "bin", "astro.py")


def _astro_module():
    spec = importlib.util.spec_from_file_location("astro", ASTRO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


astro = _astro_module()


def _utc(y, m, d, hh=0, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.timezone.utc)


class TestJulianDate(unittest.TestCase):
    def test_j2000_epoch_is_exact(self):
        # J2000.0 is BY DEFINITION 2000-01-01 12:00 UTC = JD 2451545.0.
        self.assertAlmostEqual(astro.julian_date(_utc(2000, 1, 1, 12, 0)), 2451545.0, places=6)

    def test_one_day_later_is_one_jd_later(self):
        jd1 = astro.julian_date(_utc(2026, 3, 15, 6, 0))
        jd2 = astro.julian_date(_utc(2026, 3, 16, 6, 0))
        self.assertAlmostEqual(jd2 - jd1, 1.0, places=6)


class TestGmst(unittest.TestCase):
    def test_sidereal_day_rate(self):
        # GMST advances ~360.9856 deg per UTC day (sidereal vs solar day) --
        # the defining constant of the formula itself, so this checks
        # internal arithmetic rather than the constant's provenance.
        jd = astro.julian_date(_utc(2026, 1, 1))
        g1 = astro.gmst_degrees(jd)
        g2 = astro.gmst_degrees(jd + 1.0)
        self.assertAlmostEqual((g2 - g1) % 360.0, 360.9856 % 360.0, places=2)


class TestSolarPosition(unittest.TestCase):
    def test_june_solstice_declination_near_max_north(self):
        jd = astro.julian_date(_utc(2026, 6, 21, 12, 0))
        dec, _, _, _ = astro.solar_position(jd)
        self.assertGreater(dec, 22.5)
        self.assertLess(dec, 23.6)

    def test_december_solstice_declination_near_max_south(self):
        jd = astro.julian_date(_utc(2026, 12, 21, 12, 0))
        dec, _, _, _ = astro.solar_position(jd)
        self.assertLess(dec, -22.5)
        self.assertGreater(dec, -23.6)

    def test_march_equinox_declination_near_zero(self):
        jd = astro.julian_date(_utc(2026, 3, 20, 12, 0))
        dec, _, _, _ = astro.solar_position(jd)
        self.assertLess(abs(dec), 1.0)

    def test_september_equinox_declination_near_zero(self):
        jd = astro.julian_date(_utc(2026, 9, 23, 0, 0))
        dec, _, _, _ = astro.solar_position(jd)
        self.assertLess(abs(dec), 1.0)

    def test_subsolar_longitude_near_zero_at_utc_noon(self):
        # Equation of time keeps this within a few degrees of 0 deg at
        # 12:00 UTC year-round (max excursion ~4 deg / 16 minutes).
        jd = astro.julian_date(_utc(2026, 4, 10, 12, 0))
        _, sublon, _, _ = astro.solar_position(jd)
        self.assertLess(abs(sublon), 5.0)

    def test_subsolar_longitude_near_180_at_utc_midnight(self):
        jd = astro.julian_date(_utc(2026, 4, 10, 0, 0))
        _, sublon, _, _ = astro.solar_position(jd)
        self.assertGreater(abs(sublon), 175.0)


class TestLunarPosition(unittest.TestCase):
    SAMPLE_DATES = [_utc(2026, m, 15, 6, 0) for m in range(1, 13)]

    def test_declination_stays_within_obliquity_plus_inclination(self):
        # Moon's orbital inclination (5.145 deg) added to the ecliptic's
        # obliquity (23.44 deg) bounds declination at ~28.6 deg; give the
        # two-body approximation a little headroom.
        for dt in self.SAMPLE_DATES:
            jd = astro.julian_date(dt)
            dec, sublon, _, _ = astro.lunar_position(jd)
            self.assertLess(abs(dec), 29.5, dt)
            self.assertGreaterEqual(sublon, -180.0, dt)
            self.assertLessEqual(sublon, 180.0, dt)

    def test_mean_anomaly_period_matches_anomalistic_month(self):
        # The model's own internal rates (mean longitude rate minus
        # perigee precession rate) must reproduce the textbook
        # anomalistic month (27.55455 days) -- this is a hard physical
        # identity for *any* correctly-built two-body precessing model,
        # not a fact about any one epoch/date.
        period = 360.0 / (astro.MOON_MEAN_LONGITUDE_RATE - astro.MOON_PERIGEE_RATE)
        self.assertAlmostEqual(period, 27.55455, places=1)

    def test_synodic_period_matches_known_month_length(self):
        period = 360.0 / (astro.MOON_MEAN_LONGITUDE_RATE - astro.SUN_MEAN_LONGITUDE_RATE)
        self.assertAlmostEqual(period, 29.53059, places=1)


class TestMoonPhase(unittest.TestCase):
    def test_illuminated_fraction_always_in_valid_range(self):
        for m in range(1, 13):
            jd = astro.julian_date(_utc(2026, m, 10, 0, 0))
            phase = astro.moon_phase(jd)
            self.assertGreaterEqual(phase["illuminated_fraction"], 0.0)
            self.assertLessEqual(phase["illuminated_fraction"], 1.0)

    def test_phase_cycles_back_after_one_synodic_month(self):
        jd = astro.julian_date(_utc(2026, 5, 1, 0, 0))
        p1 = astro.moon_phase(jd)
        p2 = astro.moon_phase(jd + 29.53059)
        self.assertAlmostEqual(p1["illuminated_fraction"], p2["illuminated_fraction"], delta=0.05)

    def test_phase_name_is_one_of_the_eight_standard_names(self):
        names = {"New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
                 "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent"}
        for m in range(1, 13):
            jd = astro.julian_date(_utc(2026, m, 10, 0, 0))
            phase = astro.moon_phase(jd)
            self.assertIn(phase["phase_name"], names)

    def test_age_days_within_one_synodic_month(self):
        for m in range(1, 13):
            jd = astro.julian_date(_utc(2026, m, 10, 0, 0))
            phase = astro.moon_phase(jd)
            self.assertGreaterEqual(phase["age_days"], 0.0)
            self.assertLess(phase["age_days"], 29.6)


class TestTerminatorPolygon(unittest.TestCase):
    def test_polygon_points_within_valid_lat_lon_ranges(self):
        jd = astro.julian_date(_utc(2026, 6, 21, 12, 0))
        poly = astro.terminator_polygon(jd, steps=60)
        for lat, lon in poly:
            self.assertGreaterEqual(lat, -90.0)
            self.assertLessEqual(lat, 90.0)
            self.assertGreaterEqual(lon, -180.0)
            self.assertLessEqual(lon, 180.0)

    def test_polygon_has_enough_points_to_look_smooth(self):
        jd = astro.julian_date(_utc(2026, 3, 20, 12, 0))
        poly = astro.terminator_polygon(jd, steps=90)
        self.assertGreaterEqual(len(poly), 90)

    def test_closes_via_south_pole_when_sun_is_north(self):
        # Northern-hemisphere summer: subsolar point has positive
        # declination, so the (mostly dark) south polar region must be
        # part of the shaded night polygon -- i.e. it includes a
        # lat=-90 closing vertex, never lat=+90.
        jd = astro.julian_date(_utc(2026, 6, 21, 12, 0))
        poly = astro.terminator_polygon(jd, steps=60)
        lats = [lat for lat, _ in poly]
        self.assertIn(-90.0, [round(v, 3) for v in lats])
        self.assertNotIn(90.0, [round(v, 3) for v in lats])

    def test_closes_via_north_pole_when_sun_is_south(self):
        jd = astro.julian_date(_utc(2026, 12, 21, 12, 0))
        poly = astro.terminator_polygon(jd, steps=60)
        lats = [lat for lat, _ in poly]
        self.assertIn(90.0, [round(v, 3) for v in lats])
        self.assertNotIn(-90.0, [round(v, 3) for v in lats])


if __name__ == "__main__":
    unittest.main(verbosity=2)
