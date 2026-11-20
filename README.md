# satcalc

Fetches the latest TLEs for Sentinel-1C and Sentinel-1D from
<https://tle.ivanstanojevic.me> and propagates them with
[SGP4](https://pypi.org/project/sgp4/) to print position, velocity,
latitude/longitude and altitude.

## Install

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```
satcalc                           # now
satcalc 2026-11-20                # midnight UTC
satcalc "2026-11-20 12:00:00"     # times are UTC
satcalc 2026-11-20T07:00:00-05:00 # offsets are converted to UTC
```

A warning is printed when the requested time is more than 3 days from a
TLE's epoch, since SGP4 accuracy degrades with distance from the epoch.
Requires internet access to fetch TLEs.

## nextscan: last and next scan of a location

```
nextscan 47.6 -122.3                  # lat, lon (west/south negative)
nextscan 47.6 -122.3 --limit 10       # show 10 past/planned scans (default 3)
nextscan 47.6 -122.3 --when 2026-11-05
```

- **Last scan:** the newest Sentinel-1 acquisitions over the last 14 days
  whose footprint contains the point, from the
  [Copernicus Data Space catalogue](https://catalogue.dataspace.copernicus.eu)
  (no account needed).
- **Next scan:** planned Sentinel-1C/1D acquisitions whose footprint contains
  the point, from ESA's
  [Sentinel-1 acquisition plans](https://sentinels.copernicus.eu/copernicus/sentinel-1/acquisition-plans)
  (KML, about 20 days ahead, republished every few days). Times are the
  start of each planned datatake, which can be a few minutes before the
  satellite reaches the point. The newest plan file published before the
  reference time is used. Plan files are cached in `~/.cache/satcalc/plans`
  (or `$XDG_CACHE_HOME`), and the cache is used if the page is unreachable.
  ESA can add or change acquisitions after a file is published.
