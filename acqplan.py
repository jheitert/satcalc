"""Sentinel-1 acquisition plans, as published by ESA in KML files.

ESA lists the plan files on its Sentinel-1 "acquisition plans" page. Each
file covers roughly 20 days from its publication and supersedes earlier
files for that period. File names encode the covered period, so downloaded
files never change and are cached indefinitely.
"""

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

PLANS_PAGE = "https://sentinels.copernicus.eu/copernicus/sentinel-1/acquisition-plans"
DOCUMENT_URL = "https://sentinels.copernicus.eu/documents/d/sentinel/{}"
LINK_RE = re.compile(
    r"/documents/d/sentinel/(s1[a-z]_mp_user_(\d{8}t\d{6})_(\d{8}t\d{6}))",
    re.IGNORECASE)
KML = "{http://www.opengis.net/kml/2.2}"
HEADERS = {"User-Agent": "satcalc/1.0"}


class PlanError(RuntimeError):
    pass


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    path = Path(base) / "satcalc" / "plans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stamp(text):
    return datetime.strptime(text.upper(), "%Y%m%dT%H%M%S").replace(
        tzinfo=timezone.utc)


def _plan_file(match):
    name = match.group(1).lower()
    return {"name": name, "mission": name[:3].upper(),
            "start": _stamp(match.group(2)), "stop": _stamp(match.group(3))}


def list_plan_files():
    """Plan files linked from ESA's page, or the cached ones if it is down.

    Returns (files, warning) where warning is None or a message.
    """
    warning = None
    try:
        response = requests.get(PLANS_PAGE, timeout=30, headers=HEADERS)
        response.raise_for_status()
        text = response.text
    except requests.exceptions.RequestException as e:
        warning = (f"Could not read the ESA acquisition plans page ({e}); "
                   f"using cached plans.")
        text = "\n".join(f"/documents/d/sentinel/{p.stem}"
                         for p in cache_dir().glob("*.kml"))
    files = {}
    for match in LINK_RE.finditer(text):
        plan = _plan_file(match)
        files[plan["name"]] = plan
    if not files and warning is None:
        warning = ("No plan files found on the ESA acquisition plans page; "
                   "its layout may have changed.")
    return list(files.values()), warning


def select_plans(files, when, missions):
    """For each mission, the newest plan file published at or before `when`."""
    chosen = {}
    for plan in files:
        if plan["mission"] in missions and plan["start"] <= when:
            best = chosen.get(plan["mission"])
            if best is None or plan["start"] > best["start"]:
                chosen[plan["mission"]] = plan
    return chosen


def download(plan):
    """Local path of a plan file, downloading it on first use."""
    path = cache_dir() / f"{plan['name']}.kml"
    if path.exists():
        return path
    url = DOCUMENT_URL.format(plan["name"])
    try:
        response = requests.get(url, timeout=120, headers=HEADERS)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise PlanError(f"Could not download {url}: {e}")
    if b"<kml" not in response.content[:2000]:
        raise PlanError(f"{url} did not return a KML file")
    tmp = path.with_suffix(".part")
    tmp.write_bytes(response.content)
    os.replace(tmp, path)
    return path


def _parse_time(text):
    # Plan times are UTC without an offset, e.g. 2026-11-19T19:04:36
    return datetime.fromisoformat(text.strip().rstrip("Z")).replace(
        tzinfo=timezone.utc)


def parse(path):
    """Planned acquisitions in a plan file, each with its footprint rings."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise PlanError(f"Could not parse {path}: {e}")
    entries = []
    for placemark in root.iter(f"{KML}Placemark"):
        data = {d.get("name"): (d.findtext(f"{KML}value") or "").strip()
                for d in placemark.iter(f"{KML}Data")}
        if "SatelliteId" not in data or "ObservationTimeStart" not in data:
            continue
        rings = []
        for coords in placemark.iter(f"{KML}coordinates"):
            ring = []
            for point in (coords.text or "").split():
                lon, lat = point.split(",")[:2]
                ring.append((float(lon), float(lat)))
            if len(ring) >= 3:
                rings.append(ring)
        rel = data.get("OrbitRelative")
        entries.append({
            "mission": data["SatelliteId"].upper(),
            "begin": _parse_time(data["ObservationTimeStart"]),
            "end": _parse_time(data["ObservationTimeStop"]),
            "mode": data.get("Mode", "?"),
            "polarisation": data.get("Polarisation", "?"),
            "datatake": data.get("DatatakeId", "?"),
            "rel_orbit": int(rel) if rel and rel.isdigit() else rel,
            "rings": rings,
        })
    return entries


def _inside(lons, lats, lon, lat):
    """Even-odd point-in-polygon test in the lon/lat plane."""
    inside = False
    n = len(lons)
    for i in range(n):
        x1, y1 = lons[i], lats[i]
        x2, y2 = lons[(i + 1) % n], lats[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            if lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def ring_contains(ring, lat, lon):
    """Whether a footprint ring contains the point.

    Edges are straight in lon/lat. That matches real Sentinel-1 product
    footprints better than great circles: swath edges follow the curved
    ground track. Rings that cross the antimeridian are unwrapped first.
    """
    lats = [y for _, y in ring]
    if not min(lats) <= lat <= max(lats):
        return False
    # Plans sometimes give longitudes outside ±180 (e.g. -265.9), so start
    # from a normalised vertex and keep each step under 180°.
    lons = [(ring[0][0] + 180.0) % 360.0 - 180.0]
    for x, _ in ring[1:]:
        lons.append(x + 360.0 * round((lons[-1] - x) / 360.0))
    return any(_inside(lons, lats, test, lat)
               for test in (lon, lon + 360.0, lon - 360.0))


def covering(entries, lat, lon, start, end):
    """Planned acquisitions overlapping [start, end] whose footprint has the point."""
    return [e for e in entries
            if e["end"] >= start and e["begin"] <= end
            and any(ring_contains(r, lat, lon) for r in e["rings"])]


def current_plans(when, missions):
    """Load the plan in force at `when` for each mission.

    Returns (plans, warnings), where plans maps mission -> (file, entries).
    """
    files, warning = list_plan_files()
    warnings = [warning] if warning else []
    plans = {}
    for mission, plan in sorted(select_plans(files, when, missions).items()):
        try:
            plans[mission] = (plan, parse(download(plan)))
        except PlanError as e:
            warnings.append(str(e))
    for mission in sorted(set(missions) - set(plans)):
        warnings.append(f"No acquisition plan available for {mission}.")
    return plans, warnings
