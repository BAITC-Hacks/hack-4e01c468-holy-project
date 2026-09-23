"""Load turbine CSVs and build an auditable UTC hourly observation table."""

from __future__ import annotations

import json
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


SOURCE_TIME_COLUMN = "Статистическое время"
SOURCE_COLUMNS = {
    SOURCE_TIME_COLUMN: "timestamp",
    "Нормализованная активная мощность": "power",
    "Средняя скорость ветра(m/s)": "wind_speed",
    "Средняя температура окружающей среды(°C)": "temperature",
}
RAW_COLUMNS = ["turbine_id", "timestamp", "power", "wind_speed", "temperature"]
NUMERIC_COLUMNS = ["power", "wind_speed", "temperature"]
HOURLY_COLUMNS = [
    "turbine_id",
    "hour_start",
    "available_at",
    "power",
    "wind_speed",
    "temperature",
    "observation_count",
    "quality_flag",
]
SOURCE_OFFSET = timezone(timedelta(hours=5))


def validate_hourly_history(frame: pd.DataFrame) -> None:
    """Validate the fixed UTC hourly observation schema.

    Empty histories are valid when every source timestamp is unusable; callers
    still receive the same columns and timestamp/numeric dtypes as non-empty
    histories.
    """
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("invalid_hourly_history: expected a DataFrame")
    if list(frame.columns) != HOURLY_COLUMNS:
        raise ValueError("invalid_hourly_history: columns do not match hourly schema")

    for column in ("hour_start", "available_at"):
        dtype = frame[column].dtype
        if not isinstance(dtype, pd.DatetimeTZDtype) or str(dtype.tz) != "UTC":
            raise ValueError(f"invalid_hourly_history: {column} must be UTC-aware")
        if frame[column].isna().any():
            raise ValueError(f"invalid_hourly_history: {column} contains missing timestamps")

    for column in NUMERIC_COLUMNS:
        if not pd.api.types.is_numeric_dtype(frame[column].dtype):
            raise ValueError(f"invalid_hourly_history: {column} must be numeric")
    if not pd.api.types.is_integer_dtype(frame["observation_count"].dtype):
        raise ValueError("invalid_hourly_history: observation_count must be integer")

    if frame.empty:
        return

    turbine_ids = frame["turbine_id"]
    if turbine_ids.isna().any() or not turbine_ids.map(
        lambda value: isinstance(value, str) and bool(value.strip())
    ).all():
        raise ValueError("invalid_hourly_history: turbine_id must be a non-empty string")
    if frame.duplicated(["turbine_id", "hour_start"]).any():
        raise ValueError("invalid_hourly_history: duplicate turbine/hour key")

    hour_start = frame["hour_start"]
    if (
        hour_start.dt.minute.ne(0)
        | hour_start.dt.second.ne(0)
        | hour_start.dt.microsecond.ne(0)
        | hour_start.dt.nanosecond.ne(0)
    ).any():
        raise ValueError("invalid_hourly_history: hour_start must be an hour boundary")
    if not frame["available_at"].equals(hour_start + pd.Timedelta(hours=1)):
        raise ValueError("invalid_hourly_history: available_at must equal hour_start plus one hour")

    counts = frame["observation_count"]
    if ((counts < 0) | (counts > 6)).any():
        raise ValueError("invalid_hourly_history: observation_count must be between 0 and 6")
    expected_flags = pd.Series(
        np.select(
            [counts.eq(0), counts.lt(4), counts.lt(6)],
            ["missing", "invalid", "partial"],
            default="good",
        ),
        index=frame.index,
    )
    if frame["quality_flag"].isna().any() or not frame["quality_flag"].astype(
        str
    ).equals(expected_flags):
        raise ValueError("invalid_hourly_history: quality_flag is inconsistent with observation_count")

    target = frame["power"]
    usable_target = frame["quality_flag"].isin(["good", "partial"])
    if target.loc[usable_target].isna().any() or not np.isfinite(
        target.loc[usable_target].to_numpy(dtype="float64")
    ).all() or not target.loc[usable_target].between(0.0, 1.0, inclusive="both").all():
        raise ValueError("invalid_hourly_history: power must be finite and between 0 and 1")
    if target.loc[~usable_target].notna().any():
        raise ValueError("invalid_hourly_history: power must be missing for invalid or missing rows")


class DataPipeline:
    """Convert the supplied 10-minute turbine CSV files into hourly rows.

    Naive source timestamps are interpreted at a fixed UTC+05:00, matching
    the project time convention, and are emitted as timezone-aware UTC values.
    """

    def __init__(self) -> None:
        self._source_stats: dict[str, dict[str, int]] = {}
        self._quality_summary: dict[str, Any] = {}

    @staticmethod
    def _reject_conflicting_duplicates(frame: pd.DataFrame) -> None:
        """Reject duplicate timestamps whose source measurements disagree."""
        comparable = frame[["turbine_id", "timestamp"]].copy()
        for column in NUMERIC_COLUMNS:
            source_values = frame[column]
            numeric_values = pd.to_numeric(source_values, errors="coerce")
            canonical_values = numeric_values.astype(object)
            invalid_tokens = numeric_values.isna() & source_values.notna()
            canonical_values.loc[invalid_tokens] = (
                "invalid:"
                + source_values.loc[invalid_tokens].astype(str).str.strip()
            )
            comparable[column] = canonical_values

        duplicate_keys = ["turbine_id", "timestamp"]
        duplicate_rows = comparable[
            comparable["timestamp"].notna()
            & comparable.duplicated(duplicate_keys, keep=False)
        ]
        for (turbine_id, timestamp), group in duplicate_rows.groupby(
            duplicate_keys, sort=False, dropna=False
        ):
            if (group[NUMERIC_COLUMNS].nunique(dropna=False) > 1).any():
                raise ValueError(
                    "conflicting duplicate measurements for "
                    f"{turbine_id} at {timestamp.isoformat()}"
                )

    def load(self, paths: dict[str, Path]) -> pd.DataFrame:
        """Read source CSVs using their Cyrillic headers and normalize fields."""
        if not paths:
            raise ValueError("at least one turbine CSV path is required")

        frames: list[pd.DataFrame] = []
        source_stats: dict[str, dict[str, int]] = {}
        for turbine_id, path in paths.items():
            try:
                source = pd.read_csv(path, encoding="utf-8-sig")
            except pd.errors.EmptyDataError as exc:
                raise ValueError(f"empty CSV for {turbine_id}: {path}") from exc

            if source.empty:
                raise ValueError(f"empty CSV for {turbine_id}: no measurement rows")
            missing = [name for name in SOURCE_COLUMNS if name not in source.columns]
            if missing:
                raise ValueError(
                    f"missing required headers for {turbine_id}: {', '.join(missing)}"
                )

            timestamps = pd.to_datetime(
                source[SOURCE_TIME_COLUMN],
                format="%Y-%m-%d %H:%M:%S",
                errors="coerce",
            )
            timestamps = timestamps.dt.tz_localize(SOURCE_OFFSET).dt.tz_convert("UTC")

            duplicate_check = pd.DataFrame(
                {"turbine_id": turbine_id, "timestamp": timestamps}
            )
            for source_name, target_name in SOURCE_COLUMNS.items():
                if target_name != "timestamp":
                    duplicate_check[target_name] = source[source_name]
            self._reject_conflicting_duplicates(duplicate_check)

            frame = pd.DataFrame(
                {
                    "turbine_id": turbine_id,
                    "timestamp": timestamps,
                }
            )
            for source_name, target_name in SOURCE_COLUMNS.items():
                if target_name == "timestamp":
                    continue
                frame[target_name] = pd.to_numeric(
                    source[source_name], errors="coerce"
                ).astype("float64")
            frames.append(frame[RAW_COLUMNS])
            source_stats[turbine_id] = {
                "input_rows": int(len(source)),
                "invalid_timestamp_rows": int(timestamps.isna().sum()),
            }

        self._source_stats = source_stats
        return pd.concat(frames, ignore_index=True)[RAW_COLUMNS]

    def hourly(self, raw: pd.DataFrame) -> pd.DataFrame:
        """Aggregate unique valid 10-minute observations into UTC hour bins."""
        missing = [column for column in RAW_COLUMNS if column not in raw.columns]
        if missing:
            raise ValueError(f"raw data is missing required columns: {', '.join(missing)}")

        frame = raw[RAW_COLUMNS].copy()
        frame["turbine_id"] = frame["turbine_id"].astype("string").str.strip()
        if frame["turbine_id"].isna().any() or frame["turbine_id"].eq("").any():
            raise ValueError("raw data contains a missing turbine_id")

        # load() has already applied the fixed source offset. Treat direct,
        # naive DataFrame inputs as UTC, while preserving explicit offsets.
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        self._reject_conflicting_duplicates(frame)
        for column in NUMERIC_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")

        before_deduplication = len(frame)
        invalid_timestamps_by_turbine = frame.groupby("turbine_id", sort=False)[
            "timestamp"
        ].apply(lambda timestamps: int(timestamps.isna().sum()))
        frame = frame.drop_duplicates(subset=RAW_COLUMNS, keep="first").copy()
        collapsed_duplicates = before_deduplication - len(frame)

        timestamps = frame["timestamp"]
        aligned = (
            timestamps.notna()
            & timestamps.dt.minute.mod(10).eq(0)
            & timestamps.dt.second.eq(0)
            & timestamps.dt.microsecond.eq(0)
            & timestamps.dt.nanosecond.eq(0)
        )
        finite = pd.Series(True, index=frame.index)
        for column in NUMERIC_COLUMNS:
            finite &= np.isfinite(frame[column].to_numpy())
        measurement_valid = (
            finite
            & frame["power"].between(0.0, 1.0, inclusive="both")
            & frame["wind_speed"].ge(0.0)
        )
        valid = timestamps.notna() & aligned & measurement_valid

        summary_by_turbine: dict[str, dict[str, Any]] = {}
        result_parts: list[pd.DataFrame] = []
        for turbine_id, observed in frame.groupby("turbine_id", sort=True):
            known_times = observed["timestamp"].dropna()
            stats = dict(self._source_stats.get(str(turbine_id), {}))
            stats.setdefault("input_rows", int((raw["turbine_id"] == turbine_id).sum()))
            stats.setdefault(
                "invalid_timestamp_rows",
                int(invalid_timestamps_by_turbine.get(turbine_id, 0)),
            )
            stats["off_grid_timestamp_rows"] = int(
                (observed["timestamp"].notna() & ~aligned.loc[observed.index]).sum()
            )
            stats["invalid_measurement_rows"] = int((~measurement_valid.loc[observed.index]).sum())
            stats["exact_duplicate_rows_collapsed"] = int(
                (raw["turbine_id"].astype(str) == str(turbine_id)).sum()
                - len(observed)
            )
            stats["dropped_invalid_rows"] = int((~valid.loc[observed.index]).sum())
            stats["valid_observations"] = int(valid.loc[observed.index].sum())

            if known_times.empty:
                summary_by_turbine[str(turbine_id)] = stats
                continue

            first_hour = known_times.min().floor("h")
            last_hour = known_times.max().floor("h")
            hour_index = pd.date_range(first_hour, last_hour, freq="h", tz="UTC")
            valid_observed = observed.loc[valid.loc[observed.index]]
            if valid_observed.empty:
                aggregates = pd.DataFrame(
                    index=hour_index,
                    columns=[*NUMERIC_COLUMNS, "observation_count"],
                )
            else:
                aggregates = (
                    valid_observed.set_index("timestamp")
                    .resample("1h", closed="left", label="left")
                    .agg(
                        {
                            "power": "mean",
                            "wind_speed": "mean",
                            "temperature": "mean",
                            "turbine_id": "size",
                        }
                    )
                    .rename(columns={"turbine_id": "observation_count"})
                ).reindex(hour_index)

            counts = aggregates["observation_count"].fillna(0).astype("int64")
            flags = np.select(
                [counts.eq(0), counts.lt(4), counts.lt(6)],
                ["missing", "invalid", "partial"],
                default="good",
            )
            hourly = aggregates[NUMERIC_COLUMNS].copy()
            hourly.loc[counts < 4, "power"] = np.nan
            hourly["turbine_id"] = str(turbine_id)
            hourly["hour_start"] = hour_index
            hourly["available_at"] = hour_index + pd.Timedelta(hours=1)
            hourly["observation_count"] = counts.to_numpy()
            hourly["quality_flag"] = flags
            result_parts.append(hourly[HOURLY_COLUMNS])

            flag_counts = hourly["quality_flag"].value_counts().to_dict()
            stats.update(
                {
                    "hourly_rows": int(len(hourly)),
                    "quality_flags": {
                        flag: int(flag_counts.get(flag, 0))
                        for flag in ("good", "partial", "invalid", "missing")
                    },
                    "first_hour_start_utc": hour_index[0].isoformat(),
                    "last_hour_start_utc": hour_index[-1].isoformat(),
                    "last_available_at_utc": (hour_index[-1] + pd.Timedelta(hours=1)).isoformat(),
                }
            )
            summary_by_turbine[str(turbine_id)] = stats

        if result_parts:
            result = pd.concat(result_parts, ignore_index=True)[HOURLY_COLUMNS]
            result = result.sort_values(["turbine_id", "hour_start"], kind="stable").reset_index(
                drop=True
            )
        else:
            result = pd.DataFrame(
                {
                    "turbine_id": pd.Series(dtype="object"),
                    "hour_start": pd.Series(dtype="datetime64[ns, UTC]"),
                    "available_at": pd.Series(dtype="datetime64[ns, UTC]"),
                    "power": pd.Series(dtype="float64"),
                    "wind_speed": pd.Series(dtype="float64"),
                    "temperature": pd.Series(dtype="float64"),
                    "observation_count": pd.Series(dtype="int64"),
                    "quality_flag": pd.Series(dtype="object"),
                },
                columns=HOURLY_COLUMNS,
            )

        total_flags = {flag: 0 for flag in ("good", "partial", "invalid", "missing")}
        for stats in summary_by_turbine.values():
            for flag, count in stats.get("quality_flags", {}).items():
                total_flags[flag] += count
        self._quality_summary = {
            "source_timezone_policy": "fixed_UTC+05:00",
            "source_timezone_offset": "+05:00",
            "available_at_policy": "hour_start_plus_1h",
            "input_rows": int(sum(item.get("input_rows", 0) for item in summary_by_turbine.values())),
            "exact_duplicate_rows_collapsed": int(
                sum(item.get("exact_duplicate_rows_collapsed", 0) for item in summary_by_turbine.values())
            ),
            "dropped_invalid_rows": int(
                sum(item.get("dropped_invalid_rows", 0) for item in summary_by_turbine.values())
            ),
            "valid_observations": int(
                sum(item.get("valid_observations", 0) for item in summary_by_turbine.values())
            ),
            "hourly_rows": int(len(result)),
            "quality_flags": total_flags,
            "first_hour_start_utc": (
                result["hour_start"].min().isoformat() if not result.empty else None
            ),
            "last_hour_start_utc": (
                result["hour_start"].max().isoformat() if not result.empty else None
            ),
            "last_available_at_utc": (
                result["available_at"].max().isoformat() if not result.empty else None
            ),
            "turbines": summary_by_turbine,
        }
        validate_hourly_history(result)
        return result

    def prepare(self, paths: dict[str, Path], output: Path) -> pd.DataFrame:
        """Load, aggregate, and save hourly observations plus quality summary."""
        raw = self.load(paths)
        hourly = self.hourly(raw)
        output.mkdir(parents=True, exist_ok=True)
        hourly.to_csv(output / "hourly.csv", index=False)
        with (output / "quality.json").open("w", encoding="utf-8") as handle:
            json.dump(self._quality_summary, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        return hourly
