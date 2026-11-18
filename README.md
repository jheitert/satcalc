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
