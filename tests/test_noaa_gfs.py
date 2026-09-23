"""Bounded NOAA GFS range reads and point-weather contract tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape

import pandas as pd
import pytest

from wind_forecast.contracts import parse_request
from wind_forecast.infrastructure.weather.noaa_gfs import (
    DATASET_REGISTRY_URL,
    NOAA_GFS_BUCKET_URL,
    NoaaGfsWeatherProvider,
)
from wind_forecast.weather import TURBINE_COORDINATES, WEATHER_COLUMNS


_RUN = datetime(2026, 1, 31, 6, tzinfo=timezone.utc)
_ORIGIN = datetime(2026, 1, 31, 19, tzinfo=timezone.utc)
_FIELDS = (
    ("UGRD", "10 m above ground", "u", "heightAboveGround", 10, "m s**-1", -4.0),
    ("VGRD", "10 m above ground", "v", "heightAboveGround", 10, "m s**-1", -3.0),
    ("UGRD", "100 m above ground", "u", "heightAboveGround", 100, "m s**-1", -8.0),
    ("VGRD", "100 m above ground", "v", "heightAboveGround", 100, "m s**-1", -6.0),
    ("TMP", "2 m above ground", "t", "heightAboveGround", 2, "K", 273.15),
    ("PRES", "surface", "sp", "surface", 0, "Pa", 101325.0),
    ("GUST", "surface", "gust", "surface", 0, "m s**-1", 8.0),
)


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status_code = status
        self.headers = headers or {}
        self.bytes_read = 0
        self.closed = False

    def iter_content(self, chunk_size: int = 65536):
        for start in range(0, len(self.body), chunk_size):
            part = self.body[start : start + chunk_size]
            self.bytes_read += len(part)
            yield part

    def close(self) -> None:
        self.closed = True


def _grib_message(payload: dict[str, Any]) -> bytes:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    length = 16 + len(encoded) + 4
    return b"GRIB\x00\x00\x00\x02" + length.to_bytes(8, "big") + encoded + b"7777"


class _DeterministicDecoder:
    def __init__(self, mutation: dict[str, Any] | None = None) -> None:
        self.mutation = mutation or {}

    def decode(
        self, message: bytes, coordinates: tuple[tuple[str, float, float], ...]
    ) -> dict[str, Any]:
        result = json.loads(message[16:-4])
        result.update(self.mutation)
        assert len(result["points"]) == len(coordinates)
        return result


class _FakeS3:
    """Deterministic S3 transport that only serves metadata, indices and ranges."""

    def __init__(
        self,
        horizon: int,
        *,
        page_size: int = 1000,
        missing_lead: int | None = None,
    ) -> None:
        self.page_size = page_size
        self.objects: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.responses: list[_Response] = []
        self.range_problem: str | None = None
        self.listing_failures = 0
        first_lead = int((_ORIGIN - _RUN).total_seconds() // 3600)
        for lead in range(first_lead, first_lead + horizon):
            if lead == missing_lead:
                continue
            records: list[tuple[str, str, bytes]] = []
            for grib_name, level_name, short_name, type_of_level, level, units, value in _FIELDS:
                points = [
                    {
                        "latitude": 43.75,
                        "longitude": 78.5,
                        "value": value,
                    }
                    for _ in TURBINE_COORDINATES
                ]
                payload = {
                    "short_name": short_name,
                    "type_of_level": type_of_level,
                    "level": level,
                    "units": units,
                    "data_date": "20260131",
                    "data_time": 600,
                    "validity_date": ( _RUN + timedelta(hours=lead)).strftime("%Y%m%d"),
                    "validity_time": int((_RUN + timedelta(hours=lead)).strftime("%H%M")),
                    "forecast_time": lead,
                    "step_units": 1,
                    "points": points,
                }
                records.append((grib_name, level_name, _grib_message(payload)))
            data = bytearray()
            lines: list[str] = []
            for number, (grib_name, level_name, message) in enumerate(records, start=1):
                offset = len(data)
                lines.append(
                    f"{number}:{offset}:d=2026013106:{grib_name}:{level_name}:{lead} hour fcst"
                )
                data.extend(message)
            directory = (
                f"gfs.{_RUN:%Y%m%d}/{_RUN:%H}/atmos/"
                f"gfs.t{_RUN:%H}z.pgrb2.0p25.f{lead:03d}"
            )
            publication = (
                (_RUN + timedelta(hours=3, minutes=lead % 7))
                .isoformat()
                .replace("+00:00", "Z")
            )
            self.objects[directory] = {
                "body": bytes(data),
                "etag": f"etag-{lead}-grib",
                "last_modified": publication,
            }
            self.objects[f"{directory}.idx"] = {
                "body": ("\n".join(lines) + "\n").encode(),
                "etag": f"etag-{lead}-idx",
                "last_modified": (
                    (_RUN + timedelta(hours=3, minutes=lead % 7, seconds=24))
                    .isoformat()
                    .replace("+00:00", "Z")
                ),
            }

    def get(self, url: str, **kwargs: Any) -> _Response:
        call = {"url": url, **kwargs}
        self.calls.append(call)
        parsed = urlparse(url)
        key = unquote(parsed.path.lstrip("/"))
        if url == NOAA_GFS_BUCKET_URL:
            if self.listing_failures:
                self.listing_failures -= 1
                response = _Response(b"", status=503)
                self.responses.append(response)
                return response
            params = kwargs.get("params", {})
            assert params.get("list-type") == "2"
            prefix = str(params["prefix"])
            keys = sorted(name for name in self.objects if name.startswith(prefix))
            token = str(params.get("continuation-token", "0"))
            start = int(token)
            selected = keys[start : start + self.page_size]
            truncated = start + self.page_size < len(keys)
            entries = []
            for item in selected:
                obj = self.objects[item]
                entries.append(
                    "<Contents>"
                    f"<Key>{escape(item)}</Key><LastModified>{obj['last_modified']}</LastModified>"
                    f"<ETag>\"{obj['etag']}\"</ETag><Size>{len(obj['body'])}</Size>"
                    "</Contents>"
                )
            continuation = (
                f"<NextContinuationToken>{start + self.page_size}</NextContinuationToken>"
                if truncated
                else ""
            )
            body = (
                "<ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\">"
                f"<IsTruncated>{str(truncated).lower()}</IsTruncated>{''.join(entries)}"
                f"{continuation}</ListBucketResult>"
            ).encode()
            response = _Response(body)
            self.responses.append(response)
            return response

        obj = self.objects[key]
        request_headers = kwargs.get("headers", {})
        requested_etag = request_headers.get("If-Match", "").strip('"')
        assert requested_etag == obj["etag"]
        headers = {"ETag": f'"{obj["etag"]}"'}
        range_header = request_headers.get("Range")
        if range_header is None:
            response = _Response(obj["body"], headers=headers)
            self.responses.append(response)
            return response
        start, end = (int(value) for value in range_header.removeprefix("bytes=").split("-"))
        body = obj["body"][start : end + 1]
        headers["Content-Range"] = f"bytes {start}-{end}/{len(obj['body'])}"
        status = 206
        if self.range_problem == "status":
            status = 200
            body = obj["body"]
        elif self.range_problem == "content_range":
            headers["Content-Range"] = f"bytes {start + 1}-{end + 1}/{len(obj['body'])}"
        elif self.range_problem == "etag":
            headers["ETag"] = '"wrong-version"'
        elif self.range_problem == "length":
            body = body[:-1]
        response = _Response(body, status=status, headers=headers)
        self.responses.append(response)
        return response

    def range_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if "Range" in call.get("headers", {})]


def _request(horizon: int = 24, mode: str = "competition"):
    return parse_request(_ORIGIN.isoformat(), horizon, mode)  # type: ignore[arg-type]


def _provider(tmp_path: Path, transport: _FakeS3, mutation: dict[str, Any] | None = None):
    return NoaaGfsWeatherProvider(
        tmp_path,
        transport=transport,
        decoder=_DeterministicDecoder(mutation),
    )


@pytest.mark.parametrize("horizon", [24, 48])
def test_fetch_builds_verified_hourly_two_turbine_snapshot_from_exact_forecast_fields(
    tmp_path: Path, horizon: int
) -> None:
    transport = _FakeS3(horizon, page_size=37)
    snapshot = _provider(tmp_path, transport).fetch(_request(horizon))

    assert len(snapshot.rows) == horizon * 2
    assert set(snapshot.rows.columns) == set(WEATHER_COLUMNS)
    assert set(snapshot.rows.turbine_id) == {"turbine_1", "turbine_2"}
    assert set(snapshot.rows.weather_model) == {"noaa_gfs_0p25"}
    assert snapshot.rows.groupby("turbine_id", sort=False).lead_hours.apply(list).tolist() == [
        list(range(horizon)),
        list(range(horizon)),
    ]
    assert snapshot.rows.initialized_at.eq(pd.Timestamp("2026-01-31T06:00:00Z")).all()
    assert snapshot.rows.issued_at.eq(pd.Timestamp(snapshot.provenance["available_at"])).all()
    assert snapshot.rows.available_at.eq(pd.Timestamp(snapshot.provenance["available_at"])).all()
    assert snapshot.provenance["provenance_status"] == "verified"
    assert snapshot.provenance["competition_valid"] is True
    assert snapshot.provenance["issuance_semantics"] == "public_archive_object_publication_bound"
    assert snapshot.provenance["source_url"] == NOAA_GFS_BUCKET_URL
    assert snapshot.provenance["dataset_registry_url"] == DATASET_REGISTRY_URL
    assert snapshot.provenance["initialized_at"] == "2026-01-31T06:00:00Z"
    assert snapshot.provenance["available_at"] == "2026-01-31T09:06:24Z"
    assert snapshot.provenance["model_lead_hours"] == list(range(13, 13 + horizon))
    assert len(snapshot.provenance["objects"]) == horizon * 2
    assert len(snapshot.provenance["field_ranges"]) == horizon * 7
    assert all(
        len(item["sha256"]) == len(item["index_sha256"]) == 64
        for item in snapshot.provenance["field_ranges"]
    )
    assert set(snapshot.raw_responses[0]) >= {"objects", "field_ranges"}
    row = snapshot.rows.iloc[0]
    assert row.wind_speed_10m == pytest.approx(5.0)
    assert row.wind_direction_10m == pytest.approx(53.13010235415598)
    assert row.wind_speed_100m == pytest.approx(10.0)
    assert row.temperature_2m == pytest.approx(0.0)
    assert row.surface_pressure == pytest.approx(1013.25)
    assert row.wind_gusts_10m == pytest.approx(8.0)
    assert len(transport.range_calls()) == horizon * 7
    assert all(call["headers"]["If-Match"] for call in transport.range_calls())
    assert all(call.get("stream") is True for call in transport.range_calls())
    assert all(call.get("timeout") == (5, 30) for call in transport.calls)


def test_transient_archive_listing_failures_are_retried_at_most_three_times(
    tmp_path: Path,
) -> None:
    transport = _FakeS3(24)
    transport.listing_failures = 2
    _provider(tmp_path, transport).fetch(_request(24))
    assert len([call for call in transport.calls if call["url"] == NOAA_GFS_BUCKET_URL]) == 3

    exhausted = _FakeS3(24)
    exhausted.listing_failures = 4
    with pytest.raises(ValueError, match="^noaa_gfs_listing_invalid"):
        _provider(tmp_path / "exhausted", exhausted).fetch(_request(24))
    assert len([call for call in exhausted.calls if call["url"] == NOAA_GFS_BUCKET_URL]) == 3


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"data_date": "20260130"}, "noaa_gfs_grib_run_mismatch"),
        ({"data_time": 1200}, "noaa_gfs_grib_run_mismatch"),
        ({"forecast_time": 99}, "noaa_gfs_grib_step_mismatch"),
        ({"validity_time": 1800}, "noaa_gfs_grib_valid_time_mismatch"),
        ({"short_name": "analysis"}, "noaa_gfs_grib_field_mismatch"),
        ({"type_of_level": "surface"}, "noaa_gfs_grib_field_mismatch"),
        ({"units": "knots"}, "noaa_gfs_grib_units_mismatch"),
    ],
)
def test_fetch_rejects_grib_metadata_that_does_not_match_requested_forecast(
    tmp_path: Path, mutation: dict[str, Any], reason: str
) -> None:
    transport = _FakeS3(24)

    with pytest.raises(ValueError, match=f"^{reason}"):
        _provider(tmp_path, transport, mutation).fetch(_request(24))


@pytest.mark.parametrize(
    ("kind", "reason"),
    [("missing", "noaa_gfs_object_missing"), ("future", "noaa_gfs_object_time_invalid")],
)
def test_fetch_rejects_missing_forecast_hours_and_post_origin_objects(
    tmp_path: Path, kind: str, reason: str
) -> None:
    transport = _FakeS3(24, missing_lead=17 if kind == "missing" else None)
    if kind == "future":
        first = next(iter(transport.objects.values()))
        first["last_modified"] = "2026-01-31T19:00:01Z"

    with pytest.raises(ValueError, match=f"^{reason}"):
        _provider(tmp_path, transport).fetch(_request(24))


def test_fetch_rejects_malformed_index_offsets_and_never_requests_whole_grib_objects(
    tmp_path: Path,
) -> None:
    transport = _FakeS3(24)
    idx_key = next(key for key in transport.objects if key.endswith(".idx"))
    transport.objects[idx_key]["body"] = (
        b"1:999999999:d=2026013106:UGRD:10 m above ground:13 hour fcst\n"
    )

    with pytest.raises(ValueError, match="^noaa_gfs_index_offset_invalid"):
        _provider(tmp_path, transport).fetch(_request(24))

    assert all("Range" in call.get("headers", {}) for call in transport.range_calls())


@pytest.mark.parametrize("response_problem", ["status", "content_range", "etag", "length"])
def test_range_response_must_be_partial_exact_and_version_pinned(
    tmp_path: Path, response_problem: str
) -> None:
    transport = _FakeS3(24)
    transport.range_problem = response_problem
    provider = _provider(tmp_path, transport)

    with pytest.raises(ValueError, match="^noaa_gfs_range_response_invalid"):
        provider.fetch(_request(24))

    if response_problem == "status":
        response = transport.responses[-1]
        assert response.status_code == 200
        assert response.bytes_read == 0  # A full-file 200 body is rejected before consumption.


def test_read_cached_performs_no_network_and_rejects_modified_cache_payload(tmp_path: Path) -> None:
    provider = _provider(tmp_path, _FakeS3(24))
    first = provider.fetch(_request(24))

    class _NoNetwork:
        def get(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("read_cached attempted a network request")

    cached = NoaaGfsWeatherProvider(
        tmp_path, transport=_NoNetwork(), decoder=_DeterministicDecoder()
    )
    restored = cached.read_cached(_request(24))
    assert restored is not None
    assert restored.fingerprint == first.fingerprint
    assert restored.rows.equals(first.rows)

    cache_file = next((tmp_path / "noaa-gfs").glob("*.json"))
    payload = json.loads(cache_file.read_text())
    payload["rows"][0]["wind_speed_10m"] = 999.0
    cache_file.write_text(json.dumps(payload))
    assert cached.read_cached(_request(24)) is None
    recovered = provider.fetch(_request(24))
    assert recovered.fingerprint == first.fingerprint
    assert cached.read_cached(_request(24)) is not None


def test_refresh_rechecks_archive_versions_and_reuses_unchanged_point_extract(
    tmp_path: Path,
) -> None:
    transport = _FakeS3(24)
    provider = _provider(tmp_path, transport)
    original = provider.fetch(_request(24))
    range_count = len(transport.range_calls())

    reused = provider.fetch(_request(24), refresh=True)
    assert reused.fingerprint == original.fingerprint
    assert len(transport.range_calls()) == range_count


def test_refresh_of_changed_object_version_builds_a_new_snapshot_identity(tmp_path: Path) -> None:
    transport = _FakeS3(24)
    provider = _provider(tmp_path, transport)
    original = provider.fetch(_request(24))
    changed_key = next(
        key for key in transport.objects if ".f013" in key and not key.endswith(".idx")
    )
    transport.objects[changed_key]["etag"] = "replacement-version"

    refreshed = provider.fetch(_request(24), refresh=True)
    assert refreshed.fingerprint != original.fingerprint
    assert len(transport.range_calls()) > 24 * 7


def test_decoder_fixture_matches_public_eccodes_message_api_without_network() -> None:
    eccodes = pytest.importorskip("eccodes")
    from wind_forecast.infrastructure.weather.noaa_gfs import _EcCodesDecoder, _make_message

    handle = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        for key, value in (
            ("gridType", "regular_ll"),
            ("Ni", 2),
            ("Nj", 2),
            ("latitudeOfFirstGridPointInDegrees", 44.0),
            ("longitudeOfFirstGridPointInDegrees", 78.0),
            ("iDirectionIncrementInDegrees", 1.0),
            ("jDirectionIncrementInDegrees", 1.0),
            ("iScansNegatively", 0),
            ("jScansPositively", 0),
            ("shortName", "10u"),
            ("typeOfLevel", "heightAboveGround"),
            ("level", 10),
            ("dataDate", 20260131),
            ("dataTime", 600),
            ("forecastTime", 13),
        ):
            eccodes.codes_set(handle, key, value)
        eccodes.codes_set_values(handle, [-4.0, -4.0, -4.0, -4.0])
        message = eccodes.codes_get_message(handle)
    finally:
        eccodes.codes_release(handle)

    _make_message(message)
    result = _EcCodesDecoder().decode(message, (TURBINE_COORDINATES[0],))
    assert result["short_name"] in {"10u", "u"}
    assert result["type_of_level"] == "heightAboveGround"
    assert result["level"] == 10
    assert result["units"] == "m s**-1"
    assert result["forecast_time"] == 13
    assert len(result["points"]) == 1
    assert result["points"][0]["value"] == pytest.approx(-4.0)
