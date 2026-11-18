#!/usr/bin/env python3
"""Fetch current TLEs for Sentinel-1C/1D and propagate them with SGP4."""

import argparse
import math
from datetime import datetime, timezone

import requests
from sgp4.api import Satrec, jday

SATELLITES = {
    "Sentinel-1C": 62261,
    "Sentinel-1D": 66315,
}
MAX_TLE_AGE_DAYS = 3.0  # beyond this, SGP4 error grows past a few km
TLE_API = "https://tle.ivanstanojevic.me/api/tle/{}"


def fetch_tle(norad_id):
    """Return (name, line1, line2) for a NORAD catalog number."""
    url = TLE_API.format(norad_id)
    try:
        response = requests.get(url, timeout=10,
                                headers={"User-Agent": "satcalc/1.0"})
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise SystemExit(f"Error fetching {url}: {e}")

    try:
        data = response.json()
    except ValueError:
        raise SystemExit(f"Response from {url} is not valid JSON")

    try:
        return data["name"], data["line1"], data["line2"]
    except KeyError as e:
        raise SystemExit(f"Response from {url} missing field {e}")


def teme_to_geodetic(r, jd, fr):
    """Approximate lat/lon/alt (deg, deg, km) from a TEME position.

    Rotates by GMST only (ignores polar motion/equation of the equinoxes),
    good to a few hundred metres, which is fine for a ground-track readout.
    """
    t = (jd - 2451545.0 + fr) / 36525.0
    gmst = math.radians((280.46061837 + 360.98564736629 * (jd - 2451545.0 + fr)
                         + 0.000387933 * t * t) % 360.0)
    x, y, z = r
    xe = x * math.cos(gmst) + y * math.sin(gmst)
    ye = -x * math.sin(gmst) + y * math.cos(gmst)

    # WGS-84 iterative geodetic latitude
    a, f = 6378.137, 1 / 298.257223563
    e2 = f * (2 - f)
    p = math.hypot(xe, ye)
    lon = math.atan2(ye, xe)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(5):
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        alt = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - e2 * n / (n + alt)))
    return math.degrees(lat), math.degrees(lon), alt


def parse_utc(text):
    """Parse an ISO-8601 date/time, treating it as UTC unless an offset is given."""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date {text!r}; use e.g. 2026-11-18 or 2026-11-18T16:30:00")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("when", nargs="?", type=parse_utc,
                        help="UTC date/time, e.g. 2026-11-18 or "
                             "'2026-11-18 16:30:00' (default: now)")
    args = parser.parse_args()
    now = args.when or datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute,
                  now.second + now.microsecond / 1e6)
    print(f"Epoch (UTC): {now:%Y-%m-%d %H:%M:%S}\n")

    for label, norad_id in SATELLITES.items():
        name, line1, line2 = fetch_tle(norad_id)
        print(f"TLE for {label} ({name}):\n{line1}\n{line2}")

        sat = Satrec.twoline2rv(line1, line2)
        age_days = (jd - sat.jdsatepoch) + (fr - sat.jdsatepochF)
        if abs(age_days) > MAX_TLE_AGE_DAYS:
            direction = "after" if age_days > 0 else "before"
            print(f"  WARNING: requested time is {abs(age_days):.1f} days "
                  f"{direction} the TLE epoch; position may be off by tens "
                  f"of km or more.")
        err, r, v = sat.sgp4(jd, fr)
        if err:
            print(f"  SGP4 error code {err}\n")
            continue

        lat, lon, alt = teme_to_geodetic(r, jd, fr)
        speed = math.sqrt(sum(c * c for c in v))
        print(f"  TEME r (km):   {r[0]:12.3f} {r[1]:12.3f} {r[2]:12.3f}")
        print(f"  TEME v (km/s): {v[0]:12.6f} {v[1]:12.6f} {v[2]:12.6f}")
        print(f"  lat {lat:8.3f}°  lon {lon:9.3f}°  alt {alt:8.2f} km  "
              f"speed {speed:.3f} km/s\n")


if __name__ == "__main__":
    main()
