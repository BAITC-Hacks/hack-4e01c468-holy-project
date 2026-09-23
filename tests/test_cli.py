from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def _source_data(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    local_times = pd.date_range("2026-01-24 00:00", "2026-02-01 00:00", freq="10min", inclusive="left")
    for index, turbine in enumerate(("turbine_1", "turbine_2"), start=1):
        frame = pd.DataFrame(
            {
                "Статистическое время": local_times.strftime("%Y-%m-%d %H:%M:%S"),
                "Средняя скорость ветра(m/s)": [5.0 + index] * len(local_times),
                "Нормализованная активная мощность": [0.35 + index * 0.05] * len(local_times),
                "Средняя температура окружающей среды(°C)": [-3.0] * len(local_times),
            }
        )
        frame.to_csv(directory / f"{turbine}.csv", index=False)


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    clean_env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
    if env:
        clean_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "wind_forecast.cli", *args],
        cwd=ROOT,
        env=clean_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_help_lists_all_operational_commands() -> None:
    result = _run_cli("--help")

    assert result.returncode == 0
    assert all(command in result.stdout for command in ("prepare", "train", "backtest", "run", "simulate"))


def test_naive_origin_is_rejected_as_invalid_arguments() -> None:
    result = _run_cli(
        "run",
        "--origin",
        "2026-02-01T00:00:00",
        "--mode",
        "demo",
        "--offline",
    )

    assert result.returncode == 2
    assert "origin must include a timezone" in result.stderr


def test_prepare_accepts_path_options_after_subcommand(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _source_data(data_dir)
    cache_dir = tmp_path / "cache"

    result = _run_cli(
        "prepare",
        "--data-dir",
        str(data_dir),
        "--cache-dir",
        str(cache_dir),
    )

    assert result.returncode == 0, result.stderr
    assert (cache_dir / "prepared" / "hourly.csv").is_file()
    assert "prepared" in result.stdout.lower()


def test_synthetic_weather_requires_demo_mode() -> None:
    result = _run_cli("run", "--origin", "2026-02-01T00:00:00+05:00", "--offline")

    assert result.returncode == 2
    assert "offline weather requires --mode demo" in result.stderr


def test_weather_fixture_runs_only_at_its_recorded_demo_origin(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _source_data(data_dir)
    run_dir = tmp_path / "runs"
    fixture = ROOT / "tests/fixtures/weather/synthetic_48h.json"

    result = _run_cli(
        "run",
        "--mode",
        "demo",
        "--weather-fixture",
        str(fixture),
        "--origin",
        "2026-01-31T19:00:00Z",
        "--horizon",
        "24",
        "--data-dir",
        str(data_dir),
        "--cache-dir",
        str(tmp_path / "cache"),
        "--model-dir",
        str(tmp_path / "models"),
        "--run-dir",
        str(run_dir),
    )

    assert result.returncode == 0, result.stderr
    assert "status=degraded mode=demo competition_valid=false" in result.stdout
    latest = json.loads((run_dir / "latest.json").read_text())
    manifest = json.loads((run_dir / latest["directory"] / "manifest.json").read_text())
    assert manifest["weather_provenance"]["provenance_status"] == "synthetic"
    assert manifest["competition_valid"] is False


def test_prepared_hourly_cache_is_invalidated_by_source_content_change(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _source_data(data_dir)
    cache_dir = tmp_path / "cache"
    first = _run_cli("prepare", "--data-dir", str(data_dir), "--cache-dir", str(cache_dir))
    assert first.returncode == 0, first.stderr
    output = cache_dir / "prepared" / "hourly.csv"
    first_hash = hashlib.sha256(output.read_bytes()).hexdigest()

    source = data_dir / "turbine_1.csv"
    changed = pd.read_csv(source)
    changed.loc[0, "Нормализованная активная мощность"] = 0.9
    changed.to_csv(source, index=False)
    second = _run_cli("prepare", "--data-dir", str(data_dir), "--cache-dir", str(cache_dir))

    assert second.returncode == 0, second.stderr
    assert hashlib.sha256(output.read_bytes()).hexdigest() != first_hash
