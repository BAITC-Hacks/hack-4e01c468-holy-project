from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from wind_forecast.artifacts import ArtifactStore
from wind_forecast.contracts import Prediction, parse_request
from wind_forecast.weather import make_synthetic_weather_snapshot


FORECAST_COLUMNS = [
    "run_id", "forecast_origin", "turbine_id", "valid_time", "lead_hours",
    "p10", "p50", "p90", "weather_issued_at", "weather_model", "run_status",
]


def _inputs(*, model_name: str = "catboost", data_hash: str = "data-a",
            config_hash: str = "config-a", weather_seed: int = 3):
    request = parse_request("2026-02-01T00:00:00+05:00", 24, "demo")
    snapshot = make_synthetic_weather_snapshot(request, seed=weather_seed)
    rows = []
    for turbine_id in ("turbine_1", "turbine_2"):
        for lead in range(request.horizon):
            rows.append({
                "turbine_id": turbine_id,
                "valid_time": pd.Timestamp(request.origin) + pd.Timedelta(hours=lead),
                "lead_hours": lead,
                "p10": 0.2,
                "p50": 0.4,
                "p90": 0.6,
            })
    prediction = Prediction(
        pd.DataFrame(rows),
        model_name,
        {"training_rows": 100, "metrics": {"mae": float("nan")},
         "model_parameters": {"iterations": 20}, "seed": 11},
    )
    events = [{
        "schema_version": 1, "run_id": "", "sequence": 1, "state": "quality_gate",
        "action": "continue", "reason": "valid forecast", "timestamp": "2026-01-31T19:00:00Z",
        "duration_ms": 2, "outcome": "success",
    }]
    manifest = {
        "schema_version": 1,
        "run_id": "",
        "forecast_origin": "2026-01-31T19:00:00Z",
        "horizon": 24,
        "mode": "demo",
        "status": "degraded",
        "competition_valid": True,
        "created_at": "2026-01-31T19:00:00Z",
        "parent_run_id": None,
        "fingerprints": {
            "data_contents": data_hash,
            "canonical_weather": snapshot.fingerprint,
            "config": config_hash,
            "feature_schema": "features-a",
            "model_training_rows": "training-a",
        },
        "model": {"name": model_name, "parameters": {"iterations": 20}, "seed": 11,
                  "training_cutoff": "2026-01-31T19:00:00Z", "training_rows": 100,
                  "calibration_cutoff": None, "dependency_versions": {}},
        "weather_provenance": snapshot.provenance,
        "data_quality": {},
        "calibration": {},
        "files": {},
    }
    return request, prediction, snapshot, events, manifest


def test_persist_writes_six_auditable_artifacts_with_null_metrics_and_hashes(tmp_path: Path) -> None:
    request, prediction, snapshot, events, manifest = _inputs()
    secret = "sensitive-test-value"
    manifest["OPENAI_API_KEY"] = secret
    manifest["model"]["parameters"]["api_key"] = secret
    events[0]["authorization"] = secret

    result = ArtifactStore(tmp_path).persist(
        request, prediction, snapshot,
        {"metric_status": "available", "models": [{"model": "catboost", "mae": float("nan")}],
         "coverage": {"catboost": 48}},
        events, "demo report", manifest
    )

    assert result.status == "degraded"
    assert result.reused is False
    assert set(path.name for path in result.directory.iterdir()) == {
        "forecast.csv", "manifest.json", "metrics.json", "events.jsonl", "weather.json", "report.md"
    }
    forecast = pd.read_csv(result.directory / "forecast.csv")
    assert list(forecast.columns) == FORECAST_COLUMNS
    assert len(forecast) == 48
    assert forecast.loc[0, "forecast_origin"] == "2026-01-31T19:00:00Z"
    saved_manifest = json.loads((result.directory / "manifest.json").read_text(encoding="utf-8"))
    assert saved_manifest["run_id"] == result.run_id
    assert saved_manifest["competition_valid"] is False
    for filename, expected_hash in saved_manifest["files"].items():
        assert hashlib.sha256((result.directory / filename).read_bytes()).hexdigest() == expected_hash
    metrics_text = (result.directory / "metrics.json").read_text(encoding="utf-8")
    assert "NaN" not in metrics_text and "Infinity" not in metrics_text
    assert json.loads(metrics_text)["models"][0]["mae"] is None
    weather = json.loads((result.directory / "weather.json").read_text(encoding="utf-8"))
    assert weather["raw_responses"] == snapshot.raw_responses
    assert (tmp_path / "latest.json").is_file()
    trace = [json.loads(line) for line in (result.directory / "events.jsonl").read_text().splitlines()]
    assert trace[-1]["state"] == "persist_run"
    assert trace[-1]["run_id"] == result.run_id
    assert trace[-1]["duration_ms"] >= 0
    assert secret not in "".join(path.read_text(encoding="utf-8") for path in result.directory.iterdir())


def test_identical_inputs_reuse_and_changed_config_creates_child_run(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    values = _inputs()
    request, prediction, snapshot, events, manifest = values
    first = store.persist(request, prediction, snapshot, {}, events, "report", manifest)
    same = store.persist(request, prediction, snapshot, {}, events, "report", manifest)

    assert same.run_id == first.run_id
    assert same.reused is True

    _, prediction2, snapshot2, events2, manifest2 = _inputs(config_hash="config-b")
    changed = store.persist(request, prediction2, snapshot2, {}, events2, "report", manifest2)
    saved = json.loads((changed.directory / "manifest.json").read_text(encoding="utf-8"))
    assert changed.run_id != first.run_id
    assert saved["parent_run_id"] == first.run_id


def test_check_for_updates_reuses_only_matching_fingerprints_and_valid_forecast(tmp_path: Path) -> None:
    request, prediction, snapshot, events, manifest = _inputs()
    manifest["fingerprints"].update(
        {"predictor_config": "config-id", "predictor_model": "model-id"}
    )
    store = ArtifactStore(tmp_path)
    first = store.persist(request, prediction, snapshot, {}, events, "report", manifest)

    matching = store.check_for_updates(
        request,
        snapshot,
        {
            "data_contents": manifest["fingerprints"]["data_contents"],
            "canonical_weather": manifest["fingerprints"]["canonical_weather"],
            "predictor_config": "config-id",
            "predictor_model": "model-id",
        },
    )
    changed_model = store.check_for_updates(
        request,
        snapshot,
        {
            "data_contents": manifest["fingerprints"]["data_contents"],
            "canonical_weather": manifest["fingerprints"]["canonical_weather"],
            "predictor_config": "config-id",
            "predictor_model": "model-id-v2",
        },
    )

    assert matching is not None
    assert matching.run_id == first.run_id
    assert matching.reused is True
    assert changed_model is None
    checks = [json.loads(line) for line in (tmp_path / "update_checks.jsonl").read_text().splitlines()]
    assert checks[-1]["state"] == "check_for_updates"
    assert checks[-1]["reuse_outcome"] == "hit"
    assert checks[-1]["fingerprints"]["predictor_model"] == "model-id"

    forecast_path = first.directory / "forecast.csv"
    forecast_path.write_text("corrupt", encoding="utf-8")
    assert store.check_for_updates(
        request,
        snapshot,
        {
            "data_contents": manifest["fingerprints"]["data_contents"],
            "canonical_weather": manifest["fingerprints"]["canonical_weather"],
            "predictor_config": "config-id",
            "predictor_model": "model-id",
        },
    ) is None


@pytest.mark.parametrize(
    ("artifact_name", "damage"),
    [
        ("forecast.csv", "missing"),
        ("forecast.csv", "corrupt"),
        ("metrics.json", "corrupt"),
    ],
)
def test_persist_publishes_new_run_when_matching_artifacts_are_incomplete_or_corrupt(
    tmp_path: Path, artifact_name: str, damage: str
) -> None:
    request, prediction, snapshot, events, manifest = _inputs()
    manifest["fingerprints"].update(
        {"predictor_config": "config-id", "predictor_model": "model-id"}
    )
    store = ArtifactStore(tmp_path)
    first = store.persist(request, prediction, snapshot, {}, events, "report", manifest)
    damaged_path = first.directory / artifact_name
    if damage == "missing":
        damaged_path.rename(first.directory / "forecast.csv.saved")
        damaged_state = "forecast.csv.saved"
    else:
        damaged_path.write_text("corrupt artifact", encoding="utf-8")
        damaged_state = damaged_path.read_text(encoding="utf-8")

    assert store.check_for_updates(
        request,
        snapshot,
        {
            "data_contents": manifest["fingerprints"]["data_contents"],
            "canonical_weather": manifest["fingerprints"]["canonical_weather"],
            "predictor_config": "config-id",
            "predictor_model": "model-id",
        },
    ) is None

    replacement = store.persist(request, prediction, snapshot, {}, events, "report", manifest)

    assert replacement.run_id != first.run_id
    assert replacement.reused is False
    assert first.directory.is_dir()
    if damage == "missing":
        assert (first.directory / damaged_state).is_file()
    else:
        assert damaged_path.read_text(encoding="utf-8") == damaged_state
    forecast = pd.read_csv(replacement.directory / "forecast.csv")
    assert len(forecast) == 2 * request.horizon
    assert set(forecast["run_id"]) == {replacement.run_id}
    replacement_manifest = json.loads(
        (replacement.directory / "manifest.json").read_text(encoding="utf-8")
    )
    for filename, expected_hash in replacement_manifest["files"].items():
        actual_hash = hashlib.sha256(
            (replacement.directory / filename).read_bytes()
        ).hexdigest()
        assert actual_hash == expected_hash


def test_changed_weather_snapshot_creates_child_run(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    request, prediction, snapshot, events, manifest = _inputs()
    first = store.persist(request, prediction, snapshot, {}, events, "report", manifest)

    _, prediction2, snapshot2, events2, manifest2 = _inputs(weather_seed=8)
    second = store.persist(request, prediction2, snapshot2, {}, events2, "report", manifest2)
    saved = json.loads((second.directory / "manifest.json").read_text(encoding="utf-8"))

    assert second.run_id != first.run_id
    assert saved["parent_run_id"] == first.run_id


def test_manifest_run_id_cannot_escape_store_root(tmp_path: Path) -> None:
    request, prediction, snapshot, events, manifest = _inputs()
    manifest["run_id"] = "../../outside"

    result = ArtifactStore(tmp_path).persist(request, prediction, snapshot, {}, events, "report", manifest)

    assert result.directory.parent == tmp_path.resolve()
    assert result.run_id == result.directory.name
    assert not (tmp_path.parent / "outside").exists()


def test_success_status_requires_a_real_prediction(tmp_path: Path) -> None:
    request, _, snapshot, events, manifest = _inputs()

    with pytest.raises(ValueError, match="^artifact_success_requires_prediction_and_weather"):
        ArtifactStore(tmp_path).persist(request, None, snapshot, {}, events, "no forecast", manifest)

    assert list(tmp_path.iterdir()) == []


def test_failed_run_has_no_forecast_and_does_not_advance_latest(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    request, prediction, snapshot, events, manifest = _inputs()
    successful = store.persist(request, prediction, snapshot, {}, events, "report", manifest)
    latest_before = (tmp_path / "latest.json").read_bytes()

    failed_manifest = {**manifest, "status": "failed", "fingerprints": {
        **manifest["fingerprints"], "data_contents": "different-failed-input"
    }}
    failed = store.persist(
        request, None, snapshot, {"metric_status": "unavailable_no_labels"}, events,
        "no forecast produced", failed_manifest,
    )

    assert failed.status == "failed"
    assert not (failed.directory / "forecast.csv").exists()
    assert "forecast.csv" not in json.loads((failed.directory / "manifest.json").read_text())["files"]
    assert (tmp_path / "latest.json").read_bytes() == latest_before
    assert successful.run_id != failed.run_id


def test_concurrent_identical_persists_produce_one_directory(tmp_path: Path) -> None:
    request, prediction, snapshot, events, manifest = _inputs()
    store = ArtifactStore(tmp_path)

    def persist() -> Any:
        return store.persist(request, prediction, snapshot, {}, events, "report", manifest)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: persist(), range(2)))

    assert first.run_id == second.run_id
    assert sorted([first.reused, second.reused]) == [False, True]
    assert len([item for item in tmp_path.iterdir() if item.is_dir() and item.name.startswith("run-")]) == 1


def test_latest_pointer_is_not_created_when_atomic_pointer_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wind_forecast.artifacts as artifacts

    request, prediction, snapshot, events, manifest = _inputs()
    replace = artifacts.os.replace

    def fail_latest(source: Any, destination: Any) -> None:
        if Path(destination).name == "latest.json":
            raise OSError("disk unavailable")
        replace(source, destination)

    monkeypatch.setattr(artifacts.os, "replace", fail_latest)

    with pytest.raises(OSError, match="disk unavailable"):
        ArtifactStore(tmp_path).persist(request, prediction, snapshot, {}, events, "report", manifest)

    assert not (tmp_path / "latest.json").exists()
