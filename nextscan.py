#!/usr/bin/env python3
"""When was a location last imaged by Sentinel-1, and when is it planned next?

Past scans come from the Copernicus Data Space catalogue (products whose
footprint contains the point). Upcoming scans come from ESA's published
Sentinel-1C/1D acquisition plans (planned footprints containing the point),
which run about 20 days ahead.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

import acqplan
from satcalc import parse_utc

CATALOGUE_API = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
HISTORY_DAYS = 14
LIMIT = 3                      # default number of past and planned scans
PRODUCTS_PER_SCAN = 10         # catalogue products to request per scan
MISSIONS = {"S1C", "S1D"}      # satellites with published acquisition plans


# --- time helpers ---------------------------------------------------------

def parse_api_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def odata_time(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ago(delta):
    """Human-readable duration like '1d 13h' or '3h 05m'."""
    secs = int(abs(delta.total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {rem // 60:02d}m"


# --- past scans (Copernicus) ----------------------------------------------

def fetch_scans(lat, lon, start, end, limit):
    """Up to `limit` Sentinel-1 acquisitions covering (lat, lon), newest first.

    The catalogue lists one product per processing level (RAW, SLC, GRD, ...)
    for each acquisition, so products are grouped by datatake.
    """
    point = f"geography'SRID=4326;POINT({lon} {lat})'"
    params = {
        "$filter": (f"Collection/Name eq 'SENTINEL-1'"
                    f" and OData.CSC.Intersects(area={point})"
                    f" and ContentDate/Start gt {odata_time(start)}"
                    f" and ContentDate/Start lt {odata_time(end)}"),
        "$orderby": "ContentDate/Start desc",
        "$top": str(PRODUCTS_PER_SCAN * limit),
        "$expand": "Attributes",
    }
    try:
        response = requests.get(CATALOGUE_API, params=params, timeout=60,
                                headers={"User-Agent": "satcalc/1.0"})
        response.raise_for_status()
        products = response.json()["value"]
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        raise RuntimeError(f"Copernicus catalogue query failed: {e}")

    scans = {}
    for product in products:
        attrs = {a["Name"]: a.get("Value") for a in product.get("Attributes", [])}
        mission = product["Name"][:3]                       # S1A, S1C, ...
        key = (mission, attrs.get("datatakeID"), attrs.get("orbitNumber"))
        begin = parse_api_time(product["ContentDate"]["Start"])
        stop = parse_api_time(product["ContentDate"]["End"])
        scan = scans.setdefault(key, {
            "mission": mission,
            "begin": begin,
            "end": stop,
            "mode": attrs.get("operationalMode", "?"),
            "direction": attrs.get("orbitDirection", "?"),
            "rel_orbit": attrs.get("relativeOrbitNumber"),
            "products": [],
        })
        scan["begin"] = min(scan["begin"], begin)
        scan["end"] = max(scan["end"], stop)
        scan["products"].append(product["Name"])
    newest = sorted(scans.values(), key=lambda s: s["begin"], reverse=True)
    return newest[:limit]


def preferred_product(scan):
    """Pick the most commonly used product name to show for a scan."""
    for kind in ("_GRDH_", "_GRDM_", "_SLC_"):
        for name in scan["products"]:
            if kind in name and "_COG" not in name:
                return name
    return scan["products"][0]


# --- output ---------------------------------------------------------------

def fmt_lat_lon(lat, lon):
    return (f"{abs(lat):.4f}°{'N' if lat >= 0 else 'S'} "
            f"{abs(lon):.4f}°{'E' if lon >= 0 else 'W'}")


def print_scan_line(scan):
    rel = scan["rel_orbit"] if scan["rel_orbit"] is not None else "?"
    print(f"  {scan['begin']:%Y-%m-%d %H:%M:%S}  {scan['mission']}  "
          f"{scan['mode']:<3} {scan['direction']:<10}  rel. orbit {rel}")


def print_plan_line(entry):
    print(f"  {entry['begin']:%Y-%m-%d %H:%M:%S}-{entry['end']:%H:%M:%S}  "
          f"{entry['mission']}  {entry['mode']:<3} {entry['polarisation']:<2}  "
          f"rel. orbit {entry['rel_orbit']!s:<3}  datatake {entry['datatake']}")


def latitude(text):
    value = float(text)
    if not -90 <= value <= 90:
        raise argparse.ArgumentTypeError("latitude must be between -90 and 90")
    return value


def longitude(text):
    value = float(text)
    if not -180 <= value <= 180:
        raise argparse.ArgumentTypeError(
            "longitude must be between -180 and 180")
    return value


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("lat", type=latitude, help="latitude, degrees north")
    parser.add_argument("lon", type=longitude,
                        help="longitude, degrees east (west is negative)")
    parser.add_argument("-t", "--when", type=parse_utc,
                        help="reference UTC date/time (default: now)")
    parser.add_argument("-n", "--limit", type=int, default=LIMIT,
                        help=f"past and planned scans to show "
                             f"(default: {LIMIT})")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    now = args.when or datetime.now(timezone.utc)

    # The two lookups are independent, so run them concurrently.
    with ThreadPoolExecutor(max_workers=2) as pool:
        scans_job = pool.submit(fetch_scans, args.lat, args.lon,
                                now - timedelta(days=HISTORY_DAYS), now,
                                args.limit)
        plans_job = pool.submit(acqplan.current_plans, now, MISSIONS)

    print(f"Target:   {fmt_lat_lon(args.lat, args.lon)}")
    print(f"Ref time: {now:%Y-%m-%d %H:%M:%S} UTC\n")

    # Past scans (Copernicus catalogue)
    try:
        scans = scans_job.result()
    except RuntimeError as e:
        print(f"{e}\n")
    else:
        if scans:
            last = scans[0]
            print(f"Last scan: {last['begin']:%Y-%m-%d %H:%M:%S} UTC "
                  f"({ago(now - last['begin'])} ago)")
            print_scan_line(last)
            print(f"  {preferred_product(last)}\n")
            print(f"Recent scans (newest {args.limit}, "
                  f"last {HISTORY_DAYS} days):")
            for scan in scans:
                print_scan_line(scan)
            print()
        else:
            print(f"No Sentinel-1 scans of this point in the last "
                  f"{HISTORY_DAYS} days.\n")

    # Planned scans (ESA acquisition plans)
    plans, warnings = plans_job.result()
    for warning in warnings:
        print(f"Note: {warning}")
    if warnings:
        print()
    if not plans:
        return
    plan_end = max(plan["stop"] for plan, _ in plans.values())
    planned = sorted((e for _, entries in plans.values()
                      for e in acqplan.covering(entries, args.lat, args.lon,
                                                now, plan_end)),
                     key=lambda e: e["begin"])
    coverage = ", ".join(f"{m} to {plan['stop']:%Y-%m-%d %H:%M}"
                         for m, (plan, _) in sorted(plans.items()))

    if planned:
        nxt = planned[0]
        when = (f"in {ago(nxt['begin'] - now)}" if nxt["begin"] > now
                else "in progress")
        print(f"Next planned scan: {nxt['begin']:%Y-%m-%d %H:%M:%S} UTC "
              f"({when}), {nxt['mission']} {nxt['mode']}, "
              f"rel. orbit {nxt['rel_orbit']}\n")
    print(f"Planned scans (next {args.limit}; ESA acquisition plans: "
          f"{coverage} UTC):")
    if not planned:
        print("  none")
    for entry in planned[:args.limit]:
        print_plan_line(entry)
    if len(planned) > args.limit:
        print(f"  ... {len(planned) - args.limit} more "
              f"(use --limit to show them)")


if __name__ == "__main__":
    main()
