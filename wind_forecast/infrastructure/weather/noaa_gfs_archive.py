"""Bounded NOAA S3 listing, index parsing, and ETag-pinned range reads."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

from .noaa_gfs_codec import validate_grib_message
from .noaa_gfs_common import (
    FIELD_SPECS,
    HTTP_TIMEOUT,
    MAX_GRIB_MESSAGE_BYTES,
    MAX_HTTP_ATTEMPTS,
    MAX_INDEX_BYTES,
    MAX_LIST_BYTES,
    NOAA_GFS_BUCKET_URL,
    forecast_key,
    local_xml_name,
    normalize_etag,
    parse_utc,
    prefix,
)

_CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


@dataclass(frozen=True)
class ObjectMetadata:
    key: str
    etag: str
    size: int
    last_modified: datetime

    def evidence(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "etag": self.etag,
            "size": self.size,
            "last_modified": self.last_modified.isoformat().replace("+00:00", "Z"),
        }


class NoaaGfsArchive:
    """Perform bounded reads from the one configured public NOAA bucket."""

    def __init__(self, transport: Any) -> None:
        self.transport = transport

    def _request_bytes(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int,
        expected_etag: str | None = None,
        expected_range: tuple[int, int, int] | None = None,
        expected_size: int | None = None,
        error_code: str,
    ) -> bytes:
        last_error: Exception | None = None
        for attempt in range(MAX_HTTP_ATTEMPTS):
            response = None
            try:
                response = self.transport.get(
                    url,
                    params=params,
                    headers=headers or {},
                    timeout=HTTP_TIMEOUT,
                    stream=True,
                )
                status = int(getattr(response, "status_code", 200))
                if status in (429, 500, 502, 503, 504):
                    if attempt + 1 < MAX_HTTP_ATTEMPTS:
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    raise ValueError(error_code)
                if expected_range is not None and status != 206:
                    # Reject HTTP 200 before reading a potential full GRIB object.
                    raise ValueError("noaa_gfs_range_response_invalid")
                if expected_range is None and status != 200:
                    raise ValueError(error_code)

                response_headers = getattr(response, "headers", {}) or {}
                if (
                    expected_etag is not None
                    and normalize_etag(str(response_headers.get("ETag", ""))) != expected_etag
                ):
                    code = (
                        "noaa_gfs_range_response_invalid"
                        if expected_range is not None
                        else "noaa_gfs_object_version_mismatch"
                    )
                    raise ValueError(code)
                if expected_range is not None:
                    start, end, total = expected_range
                    match = _CONTENT_RANGE_RE.fullmatch(
                        str(response_headers.get("Content-Range", ""))
                    )
                    if (
                        match is None
                        or tuple(int(value) for value in match.groups()) != (start, end, total)
                    ):
                        raise ValueError("noaa_gfs_range_response_invalid")

                body = self._read_bounded(response, max_bytes)
                if (
                    expected_range is not None
                    and len(body) != expected_range[1] - expected_range[0] + 1
                ):
                    raise ValueError("noaa_gfs_range_response_invalid")
                if expected_size is not None and len(body) != expected_size:
                    raise ValueError(error_code)
                return body
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < MAX_HTTP_ATTEMPTS:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise ValueError(error_code) from exc
            finally:
                if response is not None:
                    self._close(response)
        raise ValueError(error_code) from last_error

    @staticmethod
    def _close(response: Any) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _read_bounded(response: Any, maximum: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        iterator = getattr(response, "iter_content", None)
        if callable(iterator):
            for chunk in iterator(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum:
                    raise ValueError("noaa_gfs_response_too_large")
                chunks.append(bytes(chunk))
        else:
            body = getattr(response, "content", b"")
            if not isinstance(body, bytes) or len(body) > maximum:
                raise ValueError("noaa_gfs_response_too_large")
            chunks.append(body)
        return b"".join(chunks)

    def list_required_objects(
        self, run: datetime, origin: datetime, horizon: int
    ) -> list[ObjectMetadata]:
        key_prefix = prefix(run)
        continuation: str | None = None
        seen_tokens: set[str] = set()
        listed: dict[str, ObjectMetadata] = {}
        for _page in range(20):
            params: dict[str, Any] = {
                "list-type": "2",
                "prefix": key_prefix,
                "max-keys": "1000",
            }
            if continuation is not None:
                params["continuation-token"] = continuation
            body = self._request_bytes(
                NOAA_GFS_BUCKET_URL,
                params=params,
                max_bytes=MAX_LIST_BYTES,
                error_code="noaa_gfs_listing_invalid",
            )
            try:
                root = ET.fromstring(body)
            except ET.ParseError as exc:
                raise ValueError("noaa_gfs_listing_invalid") from exc
            contents = [node for node in root.iter() if local_xml_name(node.tag) == "Contents"]
            for content in contents:
                values: dict[str, str] = {}
                for child in content:
                    values[local_xml_name(child.tag)] = (child.text or "").strip()
                key = values.get("Key", "")
                try:
                    obj = ObjectMetadata(
                        key=key,
                        etag=normalize_etag(values["ETag"]),
                        size=int(values["Size"]),
                        last_modified=parse_utc(
                            values["LastModified"], "noaa_gfs_listing_invalid"
                        ),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("noaa_gfs_listing_invalid") from exc
                if (
                    not key.startswith(key_prefix)
                    or not obj.etag
                    or obj.size <= 0
                    or key in listed
                ):
                    raise ValueError("noaa_gfs_listing_invalid")
                listed[key] = obj

            truncated_values = [
                (node.text or "").strip().lower()
                for node in root.iter()
                if local_xml_name(node.tag) == "IsTruncated"
            ]
            if truncated_values != ["true"]:
                if truncated_values != ["false"]:
                    raise ValueError("noaa_gfs_listing_invalid")
                break
            tokens = [
                (node.text or "").strip()
                for node in root.iter()
                if local_xml_name(node.tag) == "NextContinuationToken"
            ]
            if len(tokens) != 1 or not tokens[0] or tokens[0] in seen_tokens:
                raise ValueError("noaa_gfs_listing_invalid")
            continuation = tokens[0]
            seen_tokens.add(continuation)
        else:
            raise ValueError("noaa_gfs_listing_invalid")

        expected: list[ObjectMetadata] = []
        first_lead = int((origin - run).total_seconds() // 3600)
        for lead in range(first_lead, first_lead + horizon):
            grib_key = forecast_key(run, lead)
            for key in (grib_key, f"{grib_key}.idx"):
                metadata = listed.get(key)
                if metadata is None:
                    raise ValueError("noaa_gfs_object_missing")
                if metadata.last_modified < run or metadata.last_modified > origin:
                    raise ValueError("noaa_gfs_object_time_invalid")
                expected.append(metadata)
        return expected

    def read_index(
        self, metadata: ObjectMetadata, grib_object_size: int
    ) -> tuple[bytes, list[dict[str, Any]]]:
        url = NOAA_GFS_BUCKET_URL + quote(metadata.key, safe="/")
        body = self._request_bytes(
            url,
            headers={"If-Match": f'"{metadata.etag}"'},
            max_bytes=MAX_INDEX_BYTES,
            expected_etag=metadata.etag,
            expected_size=metadata.size,
            error_code="noaa_gfs_index_response_invalid",
        )
        return body, self.parse_index(body, grib_object_size)

    @staticmethod
    def parse_index(body: bytes, object_size: int) -> list[dict[str, Any]]:
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("noaa_gfs_index_invalid") from exc
        lines = text.splitlines()
        if not lines or len(lines) > 10000:
            raise ValueError("noaa_gfs_index_invalid")
        records: list[dict[str, Any]] = []
        previous_number = 0
        previous_offset = -1
        for line in lines:
            if not line or len(line) > 4096:
                raise ValueError("noaa_gfs_index_invalid")
            parts = line.split(":", 5)
            if len(parts) != 6:
                raise ValueError("noaa_gfs_index_invalid")
            try:
                number, offset = int(parts[0]), int(parts[1])
            except ValueError as exc:
                raise ValueError("noaa_gfs_index_invalid") from exc
            if (
                number <= previous_number
                or offset <= previous_offset
                or offset < 0
                or offset >= object_size
                or not parts[2].startswith("d=")
                or not parts[3]
                or not parts[4]
            ):
                raise ValueError("noaa_gfs_index_offset_invalid")
            records.append(
                {
                    "number": number,
                    "offset": offset,
                    "reference_time": parts[2][2:],
                    "name": parts[3],
                    "level": parts[4],
                    "description": parts[5],
                }
            )
            previous_number, previous_offset = number, offset
        if records[0]["offset"] != 0:
            raise ValueError("noaa_gfs_index_offset_invalid")
        return records

    @staticmethod
    def select_index_records(
        records: list[dict[str, Any]], run: datetime, object_size: int
    ) -> dict[tuple[str, str], tuple[int, int, dict[str, Any]]]:
        wanted = {(field.index_name, field.index_level) for field in FIELD_SPECS}
        selected: dict[tuple[str, str], dict[str, Any]] = {}
        for record in records:
            if record["reference_time"] != run.strftime("%Y%m%d%H"):
                raise ValueError("noaa_gfs_index_run_mismatch")
            pair = (record["name"], record["level"])
            if pair in wanted:
                if pair in selected:
                    raise ValueError("noaa_gfs_index_duplicate_field")
                selected[pair] = record
        if set(selected) != wanted:
            raise ValueError("noaa_gfs_index_field_missing")
        offsets = [record["offset"] for record in records]
        boundaries = {
            offset: offsets[index + 1] if index + 1 < len(offsets) else object_size
            for index, offset in enumerate(offsets)
        }
        result: dict[tuple[str, str], tuple[int, int, dict[str, Any]]] = {}
        for pair, record in selected.items():
            start = record["offset"]
            end = boundaries[start]
            if end <= start or end - start > MAX_GRIB_MESSAGE_BYTES:
                raise ValueError("noaa_gfs_index_offset_invalid")
            result[pair] = (start, end - 1, record)
        return result

    def read_range(self, metadata: ObjectMetadata, start: int, end: int) -> bytes:
        url = NOAA_GFS_BUCKET_URL + quote(metadata.key, safe="/")
        body = self._request_bytes(
            url,
            headers={
                "Range": f"bytes={start}-{end}",
                "If-Match": f'"{metadata.etag}"',
            },
            max_bytes=MAX_GRIB_MESSAGE_BYTES,
            expected_etag=metadata.etag,
            expected_range=(start, end, metadata.size),
            error_code="noaa_gfs_range_response_invalid",
        )
        validate_grib_message(body)
        return body
