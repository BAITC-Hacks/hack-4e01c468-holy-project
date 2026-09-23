import json
from pathlib import Path

import pandas as pd
import pytest

from wind_forecast import data as data_module
from wind_forecast.data import DataPipeline, HOURLY_COLUMNS


HEADERS = (
    "ID,Статистическое время,Средняя скорость ветра(m/s),"
    "Нормализованная активная мощность,Средняя температура окружающей среды(°C)"
)


def write_source(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join([HEADERS, *rows]) + "\n", encoding="utf-8")
    return path


def raw_rows(times: list[str], *, power: float = 0.5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "turbine_id": ["turbine_1"] * len(times),
            "timestamp": pd.to_datetime(times, utc=True),
            "power": [power] * len(times),
            "wind_speed": [5.0] * len(times),
            "temperature": [0.0] * len(times),
        }
    )


def _validate_hourly_history(frame: pd.DataFrame) -> None:
    validator = getattr(data_module, "validate_hourly_history", None)
    assert callable(validator), "hourly history validator is not implemented"
    validator(frame)


def test_load_maps_cyrillic_headers_and_localizes_fixed_plus_five(tmp_path: Path) -> None:
    source = write_source(
        tmp_path / "turbine.csv",
        [
            "1,2026-02-01 0:00:00,5.0,0.2,1.0",
            "2,2026-02-01 00:10:00,6.0,0.4,2.0",
        ],
    )

    raw = DataPipeline().load({"turbine_1": source})

    assert list(raw.columns) == [
        "turbine_id",
        "timestamp",
        "power",
        "wind_speed",
        "temperature",
    ]
    assert raw["timestamp"].iloc[0] == pd.Timestamp("2026-01-31T19:00:00Z")
    assert raw["timestamp"].iloc[1] == pd.Timestamp("2026-01-31T19:10:00Z")


def test_partial_hour_has_end_of_hour_availability() -> None:
    raw = pd.DataFrame(
        {
            "turbine_id": ["turbine_1"] * 4,
            "timestamp": pd.date_range("2026-01-31T18:00Z", periods=4, freq="10min"),
            "power": [0.2, 0.4, 0.6, 0.8],
            "wind_speed": [5.0] * 4,
            "temperature": [0.0] * 4,
        }
    )

    row = DataPipeline().hourly(raw).iloc[0]

    assert row.observation_count == 4
    assert row.quality_flag == "partial"
    assert row.power == pytest.approx(0.5)
    assert row.hour_start == pd.Timestamp("2026-01-31T18:00:00Z")
    assert row.available_at == pd.Timestamp("2026-01-31T19:00:00Z")


def test_three_observations_and_empty_gap_hour_are_retained() -> None:
    raw = raw_rows(
        [
            "2026-01-31T18:00Z",
            "2026-01-31T18:10Z",
            "2026-01-31T18:20Z",
            "2026-01-31T20:00Z",
            "2026-01-31T20:10Z",
            "2026-01-31T20:20Z",
            "2026-01-31T20:30Z",
            "2026-01-31T20:40Z",
            "2026-01-31T20:50Z",
        ]
    )

    hourly = DataPipeline().hourly(raw).set_index("hour_start")

    assert list(hourly["observation_count"]) == [3, 0, 6]
    assert list(hourly["quality_flag"]) == ["invalid", "missing", "good"]
    assert pd.isna(hourly.iloc[0]["power"])
    assert pd.isna(hourly.iloc[1]["power"])
    assert hourly.iloc[2]["power"] == pytest.approx(0.5)


def test_duplicate_invalid_timestamps_keep_source_level_audit_count(tmp_path: Path) -> None:
    source = write_source(
        tmp_path / "turbine.csv",
        [
            "1,not-a-timestamp,5.0,0.2,1.0",
            "2,not-a-timestamp,5.0,0.2,1.0",
            "3,2026-02-01 00:00:00,5.0,0.2,1.0",
        ],
    )
    pipeline = DataPipeline()

    pipeline.hourly(pipeline.load({"turbine_1": source}))

    turbine_summary = pipeline._quality_summary["turbines"]["turbine_1"]
    assert turbine_summary["input_rows"] == 3
    assert turbine_summary["invalid_timestamp_rows"] == 2
    assert turbine_summary["exact_duplicate_rows_collapsed"] == 1


def test_hourly_history_validator_rejects_out_of_range_observation_count() -> None:
    hourly = DataPipeline().hourly(
        raw_rows([f"2026-01-31T18:{minute:02d}Z" for minute in range(0, 60, 10)])
    )
    hourly.loc[0, "observation_count"] = 7

    with pytest.raises(ValueError, match="^invalid_hourly_history: observation_count"):
        _validate_hourly_history(hourly)


def test_hourly_history_validator_rejects_naive_hour_timestamp() -> None:
    hourly = DataPipeline().hourly(
        raw_rows([f"2026-01-31T18:{minute:02d}Z" for minute in range(0, 60, 10)])
    )
    hourly["hour_start"] = hourly["hour_start"].dt.tz_localize(None)

    with pytest.raises(ValueError, match="^invalid_hourly_history: hour_start"):
        _validate_hourly_history(hourly)


def test_hourly_history_validator_rejects_duplicate_turbine_hour_key() -> None:
    hourly = DataPipeline().hourly(
        raw_rows([f"2026-01-31T18:{minute:02d}Z" for minute in range(0, 60, 10)])
    )
    duplicate = pd.concat([hourly, hourly.iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="^invalid_hourly_history: duplicate turbine/hour"):
        _validate_hourly_history(duplicate)


def test_hourly_history_validator_rejects_quality_flag_inconsistent_with_count() -> None:
    hourly = DataPipeline().hourly(
        raw_rows([f"2026-01-31T18:{minute:02d}Z" for minute in range(0, 60, 10)])
    )
    hourly.loc[0, "quality_flag"] = "partial"

    with pytest.raises(ValueError, match="^invalid_hourly_history: quality_flag"):
        _validate_hourly_history(hourly)


def test_all_invalid_timestamps_return_typed_empty_hourly_history() -> None:
    raw = pd.DataFrame(
        {
            "turbine_id": ["turbine_1"],
            "timestamp": pd.to_datetime([None], utc=True),
            "power": [0.5],
            "wind_speed": [5.0],
            "temperature": [0.0],
        }
    )

    hourly = DataPipeline().hourly(raw)

    assert hourly.empty
    assert list(hourly.columns) == HOURLY_COLUMNS
    assert str(hourly["hour_start"].dtype) == "datetime64[ns, UTC]"
    _validate_hourly_history(hourly)


def test_exact_duplicate_is_collapsed_and_shuffled_input_is_sorted() -> None:
    raw = raw_rows(
        [
            "2026-01-31T20:50Z",
            "2026-01-31T18:10Z",
            "2026-01-31T20:00Z",
            "2026-01-31T18:00Z",
            "2026-01-31T18:20Z",
            "2026-01-31T18:00Z",
            "2026-01-31T20:30Z",
            "2026-01-31T20:20Z",
            "2026-01-31T20:10Z",
            "2026-01-31T20:40Z",
        ]
    )

    hourly = DataPipeline().hourly(raw)

    assert list(hourly["observation_count"]) == [3, 0, 6]
    assert list(hourly["quality_flag"]) == ["invalid", "missing", "good"]
    assert list(hourly["hour_start"]) == list(
        pd.date_range("2026-01-31T18:00Z", periods=3, freq="h")
    )


def test_conflicting_values_at_same_turbine_timestamp_are_rejected() -> None:
    raw = raw_rows(["2026-01-31T18:00Z", "2026-01-31T18:00Z"])
    raw.loc[1, "power"] = 0.7

    with pytest.raises(ValueError, match="conflicting duplicate"):
        DataPipeline().hourly(raw)


def test_conflicting_unparseable_duplicate_values_are_rejected() -> None:
    raw = raw_rows(["2026-01-31T18:00Z", "2026-01-31T18:00Z"])
    raw["wind_speed"] = raw["wind_speed"].astype(object)
    raw.loc[0, "wind_speed"] = "bad-a"
    raw.loc[1, "wind_speed"] = "bad-b"

    with pytest.raises(ValueError, match="conflicting duplicate"):
        DataPipeline().hourly(raw)


def test_invalid_measurements_and_off_grid_timestamp_do_not_count() -> None:
    raw = raw_rows(
        [
            "2026-01-31T18:00Z",
            "2026-01-31T18:10Z",
            "2026-01-31T18:20Z",
            "2026-01-31T18:30Z",
            "2026-01-31T18:40Z",
            "2026-01-31T18:50Z",
            "2026-01-31T18:55Z",
        ]
    )
    raw["wind_speed"] = raw["wind_speed"].astype(object)
    raw.loc[1, "power"] = 1.2
    raw.loc[2, "wind_speed"] = -1.0
    raw.loc[3, "temperature"] = float("inf")
    raw.loc[4, "wind_speed"] = "not-a-number"

    row = DataPipeline().hourly(raw).iloc[0]

    assert row.observation_count == 2
    assert row.quality_flag == "invalid"
    assert pd.isna(row.power)


def test_empty_csv_raises_clear_value_error(tmp_path: Path) -> None:
    source = tmp_path / "empty.csv"
    source.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty CSV"):
        DataPipeline().load({"turbine_1": source})


def test_header_mismatch_raises_value_error(tmp_path: Path) -> None:
    source = tmp_path / "wrong.csv"
    source.write_text("ID,time,wind\n1,2026-02-01 00:00:00,5\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required headers"):
        DataPipeline().load({"turbine_1": source})


def test_prepare_writes_hourly_csv_and_quality_summary(tmp_path: Path) -> None:
    source = write_source(
        tmp_path / "turbine.csv",
        [
            "1,2026-02-01 00:00:00,5.0,0.2,1.0",
            "2,2026-02-01 00:10:00,5.0,0.4,1.0",
            "3,2026-02-01 00:20:00,5.0,0.6,1.0",
            "4,2026-02-01 00:30:00,5.0,0.8,1.0",
        ],
    )
    output = tmp_path / "prepared"

    hourly = DataPipeline().prepare({"turbine_1": source}, output)

    assert output.joinpath("hourly.csv").is_file()
    assert output.joinpath("quality.json").is_file()
    assert len(hourly) == 1
    assert pd.read_csv(output / "hourly.csv").loc[0, "quality_flag"] == "partial"


def test_prepare_reports_bad_timestamp_without_shifting_hourly_rows(tmp_path: Path) -> None:
    source = write_source(
        tmp_path / "turbine.csv",
        [
            "1,2026-02-01 00:00:00,5.0,0.2,1.0",
            "2,2026-02-01 00:10:00,5.0,0.4,1.0",
            "3,2026-02-01 00:20:00,5.0,0.6,1.0",
            "4,2026-02-01 00:30:00,5.0,0.8,1.0",
            "5,not-a-timestamp,5.0,0.8,1.0",
        ],
    )
    output = tmp_path / "prepared"

    hourly = DataPipeline().prepare({"turbine_1": source}, output)
    summary = json.loads((output / "quality.json").read_text(encoding="utf-8"))

    assert len(hourly) == 1
    assert hourly.iloc[0].observation_count == 4
    assert summary["source_timezone_policy"] == "fixed_UTC+05:00"
    assert summary["dropped_invalid_rows"] == 1
    assert summary["turbines"]["turbine_1"]["invalid_timestamp_rows"] == 1
