# NOAA GFS point weather adapter

`NoaaGfsWeatherProvider` is an opt-in weather source implementing the existing
`fetch(request, refresh=False)` / `read_cached(request)` shape. It is not wired
into application configuration or the service composition root yet.

The source is NOAA's public `noaa-gfs-bdp-pds` S3 bucket. The [AWS Open Data
Registry entry](https://registry.opendata.aws/noaa-gfs-bdp-pds/) describes GFS
and its public access. The provider selects the latest six-hour GFS cycle at
least 12 hours before the requested origin, then requests exactly the model
forecast hours covering the requested horizon. For example, an origin of
`2026-01-31T19:00Z` selects the `2026-01-31T06:00Z` cycle and reads model leads
13 through 60 for a 48-hour request.

## Bounded reads

The global GRIB objects are hundreds of megabytes. The adapter never downloads
an entire object. For each forecast hour it:

1. Lists object metadata from the fixed NOAA bucket and requires the matching
   forecast file and index.
2. Checks each required object's key, ETag, size, and S3 `LastModified`; every
   required forecast and index object must have been published between its
   model initialization and the forecast origin.
3. Reads the bounded index text with `If-Match`, selects the exact UGRD/VGRD,
   TMP, PRES, and GUST variable/level messages, and uses the next index offset
   to determine each message byte range.
4. Reads each message using a single HTTP range request with `If-Match`, then
   requires HTTP 206, the exact `Content-Range`, matching ETag, expected byte
   count, and valid GRIB message boundaries before decoding.
5. Decodes through ecCodes, validates the actual run, forecast step, valid
   time, field, level, units, and nearest grid points, then retains only the
   two turbine point values and SHA-256 evidence for each downloaded message.

Each request reads one index per model hour and seven bounded GRIB messages per
hour. Message ranges can still total hundreds of megabytes for a 48-hour
forecast. The adapter applies a 5 MiB cap per message and 2 MiB caps to index
and listing responses. Transient HTTP/network failures are retried at most
three times per request. It does not silently fall back to another source.

## Weather mapping and provenance

The input fields are UGRD/VGRD at 10 m and 100 m, TMP at 2 m, and PRES/GUST at
the surface. Wind speeds are calculated from their matching U/V pair;
10-metre direction is derived from that pair in meteorological degrees.
Temperature is converted from kelvin to Celsius and surface pressure from
pascals to hectopascals. Gust and wind components remain in metres per second.
The two turbines can map to the same 0.25-degree GFS grid cell; each actual
nearest grid coordinate is recorded in provenance so the output does not
suggest finer spatial detail than the source provides.

`initialized_at` records the decoded GFS cycle. `issued_at` and `available_at`
use the maximum `LastModified` across every required GRIB object and index.
This is a conservative public archive publication bound, recorded as
`issuance_semantics=public_archive_object_publication_bound`; it is not claimed
to be the exact NCEP model-release time. A snapshot is marked verified only
after complete coverage, pre-origin archive timestamps, version-pinned ranges,
GRIB metadata, and values all pass validation.

## Cache and use

Point extracts and audit evidence are stored atomically below
`<cache_dir>/noaa-gfs/`. The cache includes the exact object versions, source
timestamps, selected byte offsets, range hashes, decoded field metadata, and
nearest grid coordinates. Global GRIB payloads are not retained. `read_cached`
does no network I/O and rejects entries whose checksum, fingerprint, grid
evidence, row values, or weather contract no longer validate. A refresh checks
current S3 object versions and reuses the point extract if every required
object version is unchanged.

```python
from pathlib import Path

from wind_forecast.contracts import parse_request
from wind_forecast.infrastructure.weather.noaa_gfs import NoaaGfsWeatherProvider

provider = NoaaGfsWeatherProvider(Path(".cache/weather"))
request = parse_request("2026-01-31T19:00:00Z", horizon=48, mode="competition")
snapshot = provider.fetch(request)
```

The default decoder lazily imports the public Python API from [ecCodes,
developed by ECMWF](https://confluence.ecmwf.int/display/ECC/ecCodes+Home).
The application does not currently select this provider automatically; source
wiring, the first reviewed real forecast retrieval, and February metadata
acceptance remain separate integration work. The existence of prior S3
metadata probes alone is not proof that every requested hour is valid or
available before a given origin.
