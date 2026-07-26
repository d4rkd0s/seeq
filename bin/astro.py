"""Sun/Moon ephemeris for the dashboard's day/night terminator toggle and
moon-position widget (EME situational awareness) -- pure stdlib, no
network calls, consistent with SeeQ's runtime being claude-less/offline.

ACCURACY, STATED HONESTLY: the Sun position uses the standard low-precision
solar formula (Meeus-style mean longitude + equation of center from
Earth's known orbital eccentricity), good to a small fraction of a degree.
The Moon position uses a two-body Keplerian approximation of our own
construction: mean motion + an equation-of-center term derived from the
Moon's known orbital eccentricity (0.0549), with the ascending node and
perigee precessing at their well-known real periods (18.6 years / 8.85
years). This omits the Sun's own gravitational perturbations on the
Moon's orbit (evection, variation, etc. -- real effects, several tenths
of a degree each) that a full lunar theory (e.g. ELP2000) includes. We
chose this simpler model deliberately: it's built entirely from physical
constants (eccentricity, orbital periods) that can be cross-checked
against each other and against well-known month lengths (see
tools/test_astro.py), rather than a large table of perturbation
coefficients that would have to be trusted from memory with no live
reference to verify against. Net accuracy: roughly 1-2 degrees for the
Moon's position, i.e. useful for "where is it, roughly, on the map /
what phase is it" situational awareness -- NOT for blind-pointing an EME
dish. Use dedicated tracking software (rotor control via hamlib, etc.)
for that.
"""
import datetime
import math

JD2000 = 2451545.0                      # Julian Date of the J2000.0 epoch

# ---- Sun: standard low-precision mean-element formula (Meeus-style). ----
SUN_MEAN_LONGITUDE_RATE = 360.0 / 365.256363  # deg/day (sidereal year)

# ---- Moon: two-body Keplerian approximation (see module docstring). ----
MOON_ECC = 0.0549                       # orbital eccentricity
MOON_INCLINATION_DEG = 5.145            # inclination to the ecliptic
MOON_L0_DEG = 218.3164                  # mean longitude at J2000.0
MOON_NODE0_DEG = 125.0445               # longitude of ascending node at J2000.0
MOON_PERIGEE0_DEG = 83.3532             # longitude of perigee at J2000.0
MOON_MEAN_LONGITUDE_RATE = 360.0 / 27.321661   # deg/day (sidereal month)
MOON_NODE_RATE = -360.0 / 6793.48              # deg/day (18.6 yr regression)
MOON_PERIGEE_RATE = 360.0 / 3232.6             # deg/day (8.85 yr precession)

SYNODIC_MONTH_DAYS = 29.53059

PHASE_NAMES = [
    (22.5, "New Moon"), (67.5, "Waxing Crescent"), (112.5, "First Quarter"),
    (157.5, "Waxing Gibbous"), (202.5, "Full Moon"), (247.5, "Waning Gibbous"),
    (292.5, "Last Quarter"), (337.5, "Waning Crescent"), (360.1, "New Moon"),
]


def julian_date(dt):
    """Julian Date for a UTC datetime (naive datetimes are treated as UTC)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    y, m = dt.year, dt.month
    d = dt.day + (dt.hour + dt.minute / 60.0 + dt.second / 3600.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def gmst_degrees(jd):
    """Greenwich Mean Sidereal Time, in degrees (0-360)."""
    t = (jd - JD2000) / 36525.0
    gmst = (280.46061837 + 360.98564736629 * (jd - JD2000)
            + 0.000387933 * t * t - t * t * t / 38710000.0)
    return gmst % 360.0


def obliquity_deg(jd):
    """Mean obliquity of the ecliptic, in degrees."""
    t = (jd - JD2000) / 36525.0
    return 23.0 + (26.0 + 21.448 / 60.0) / 60.0 - (46.8150 * t + 0.00059 * t * t - 0.001813 * t ** 3) / 3600.0


def ecliptic_to_equatorial(lon_deg, lat_deg, eps_deg):
    """(ecliptic lon, ecliptic lat, obliquity) -> (RA deg 0-360, Dec deg)."""
    lon, lat, eps = math.radians(lon_deg), math.radians(lat_deg), math.radians(eps_deg)
    ra = math.atan2(math.sin(lon) * math.cos(eps) - math.tan(lat) * math.sin(eps), math.cos(lon))
    dec = math.asin(math.sin(lat) * math.cos(eps) + math.cos(lat) * math.sin(eps) * math.sin(lon))
    return math.degrees(ra) % 360.0, math.degrees(dec)


def _normalize_lon(lon_deg):
    """Wrap a longitude into (-180, 180]."""
    return ((lon_deg + 180.0) % 360.0) - 180.0


def _subpoint_longitude(ra_deg, jd):
    """RA of a body directly over the Earth's surface at longitude
    (RA - GMST), normalized to (-180, 180]."""
    return _normalize_lon(ra_deg - gmst_degrees(jd))


def sun_ecliptic_longitude(jd):
    """(true ecliptic longitude deg, mean anomaly deg) for the Sun."""
    t = (jd - JD2000) / 36525.0
    l0 = (280.46646 + 36000.76983 * t + 0.0003032 * t * t) % 360.0
    m = (357.52911 + 35999.05029 * t - 0.0001537 * t * t) % 360.0
    mr = math.radians(m)
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(mr)
         + (0.019993 - 0.000101 * t) * math.sin(2 * mr)
         + 0.000289 * math.sin(3 * mr))
    return (l0 + c) % 360.0, m


def solar_position(jd):
    """(declination deg, subsolar longitude deg, true ecliptic lon deg, mean anomaly deg)."""
    true_lon, mean_anomaly = sun_ecliptic_longitude(jd)
    eps = obliquity_deg(jd)
    ra, dec = ecliptic_to_equatorial(true_lon, 0.0, eps)
    return dec, _subpoint_longitude(ra, jd), true_lon, mean_anomaly


def moon_ecliptic_position(jd):
    """(ecliptic longitude deg, ecliptic latitude deg, mean anomaly deg) for
    the Moon -- two-body Keplerian approximation, see module docstring."""
    t = jd - JD2000
    mean_lon = (MOON_L0_DEG + MOON_MEAN_LONGITUDE_RATE * t) % 360.0
    node = (MOON_NODE0_DEG + MOON_NODE_RATE * t) % 360.0
    perigee = (MOON_PERIGEE0_DEG + MOON_PERIGEE_RATE * t) % 360.0
    mean_anomaly = (mean_lon - perigee) % 360.0
    ma = math.radians(mean_anomaly)
    # Equation of center from the Moon's known eccentricity (2e*sin(M') +
    # (5/4)e^2*sin(2M'), the standard two-term two-body series -- the e^3
    # term is <0.0002 deg, well under this model's overall error budget).
    eoc_deg = (math.degrees(2 * MOON_ECC) * math.sin(ma)
               + math.degrees(1.25 * MOON_ECC ** 2) * math.sin(2 * ma))
    true_anomaly = mean_anomaly + eoc_deg
    u = math.radians(true_anomaly + perigee - node)          # argument of latitude
    i = math.radians(MOON_INCLINATION_DEG)
    ecl_lon = (node + math.degrees(math.atan2(math.sin(u) * math.cos(i), math.cos(u)))) % 360.0
    ecl_lat = math.degrees(math.asin(math.sin(u) * math.sin(i)))
    return ecl_lon, ecl_lat, mean_anomaly


def lunar_position(jd):
    """(declination deg, sub-lunar longitude deg, ecliptic lon deg, ecliptic lat deg)."""
    ecl_lon, ecl_lat, _ = moon_ecliptic_position(jd)
    eps = obliquity_deg(jd)
    ra, dec = ecliptic_to_equatorial(ecl_lon, ecl_lat, eps)
    return dec, _subpoint_longitude(ra, jd), ecl_lon, ecl_lat


def _phase_name_for_elongation(elong_deg):
    for upper, name in PHASE_NAMES:
        if elong_deg < upper:
            return name
    return "New Moon"                   # unreachable (elong_deg always < 360.1)


def moon_phase(jd):
    """dict(illuminated_fraction, elongation_deg, phase_name, age_days).
    Elongation = Moon's ecliptic longitude minus the Sun's (0=new,
    180=full); illuminated fraction k=(1-cos(elongation))/2."""
    moon_lon, _, _ = moon_ecliptic_position(jd)
    sun_lon, _ = sun_ecliptic_longitude(jd)
    elongation = (moon_lon - sun_lon) % 360.0
    k = (1.0 - math.cos(math.radians(elongation))) / 2.0
    age_days = elongation / 360.0 * SYNODIC_MONTH_DAYS
    return {"illuminated_fraction": k, "elongation_deg": elongation,
            "phase_name": _phase_name_for_elongation(elongation), "age_days": age_days}


def terminator_polygon(jd, steps=181):
    """Closed [lat, lon] polygon (degrees) covering the NIGHT hemisphere,
    for shading on an equirectangular map. Built from the subsolar-point
    boundary curve lat(lon) = atan(-cos(lat_sun)*cos(lon-lon_sun) /
    sin(lat_sun)) -- standard spherical trig: the terminator is the great
    circle 90 deg from the subsolar point -- closed along whichever pole
    is in polar night this time of year."""
    lat_sun, lon_sun, _, _ = solar_position(jd)
    lat_sun_r = math.radians(lat_sun)
    sin_lat_sun = math.sin(lat_sun_r)
    if abs(sin_lat_sun) < 1e-9:
        sin_lat_sun = 1e-9 if sin_lat_sun >= 0 else -1e-9  # near-exact-equinox guard
    cos_lat_sun = math.cos(lat_sun_r)

    boundary = []
    for i in range(steps + 1):
        lon = -180.0 + 360.0 * i / steps
        h = math.radians(lon - lon_sun)
        lat = math.degrees(math.atan(-cos_lat_sun * math.cos(h) / sin_lat_sun))
        boundary.append([lat, lon])

    closing_pole = -90.0 if lat_sun >= 0 else 90.0
    return boundary + [[closing_pole, 180.0], [closing_pole, -180.0]]


def snapshot(dt=None):
    """Everything the dashboard's /astro/state endpoint needs, computed
    for one instant (defaults to now, UTC)."""
    dt = dt or datetime.datetime.now(datetime.timezone.utc)
    jd = julian_date(dt)
    sun_dec, sun_sublon, _, _ = solar_position(jd)
    moon_dec, moon_sublon, _, _ = lunar_position(jd)
    phase = moon_phase(jd)
    return {
        "utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sun": {"lat": sun_dec, "lon": sun_sublon},
        "moon": {"lat": moon_dec, "lon": moon_sublon, **phase},
        "terminator": terminator_polygon(jd),
    }
