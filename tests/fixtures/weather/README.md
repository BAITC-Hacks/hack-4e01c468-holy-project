# Weather fixture provenance

`synthetic_48h.json` is generated locally for the demo and unit tests. It is not
an Open-Meteo response and contains no observed or archived weather. Its
`provenance_status` is `synthetic`, its `competition_valid` value is false, and
its issue/availability timestamps are null by design.

The checked-in fixture was generated on 2026-09-23 from
`make_synthetic_weather_snapshot` / `write_synthetic_weather_fixture` in
`wind_forecast/weather.py`, with origin `2026-01-31T19:00:00Z`, horizon 48, and
seed 17. To generate an offline fixture for any other valid origin or horizon:

```python
from pathlib import Path
from wind_forecast.contracts import parse_request
from wind_forecast.weather import write_synthetic_weather_fixture

request = parse_request("2026-02-01T00:00:00+05:00", 48, mode="demo")
write_synthetic_weather_fixture(Path("weather.json"), request, seed=17)
```

The separate live demo provider uses the official
[Open-Meteo Single Runs API](https://open-meteo.com/en/docs/single-runs-api). Its `run` field is an
initialization time, and its archived January 2026 IFS values are labeled
`hindcast`; release and availability times remain null because the endpoint
does not prove them. The Single Runs documentation says that global forecasts
are typically distributed 4–6 hours after initialization, which is not
per-origin release evidence. See the worker report for the bounded probe result.
