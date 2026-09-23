from __future__ import annotations

import importlib
import importlib.util
from types import CodeType, FunctionType
from typing import Any

from wind_forecast import agent
from wind_forecast.models import ForecastModel
from wind_forecast.service import Application


def _relocate_code(code: CodeType) -> CodeType:
    constants = tuple(
        _relocate_code(value) if isinstance(value, CodeType) else value
        for value in code.co_consts
    )
    return code.replace(
        co_filename="/moved/source/module.py",
        co_firstlineno=code.co_firstlineno + 1000,
        co_consts=constants,
    )


def _relocated_function(function: FunctionType) -> FunctionType:
    relocated = FunctionType(
        _relocate_code(function.__code__),
        function.__globals__,
        function.__name__,
        function.__defaults__,
        function.__closure__,
    )
    relocated.__kwdefaults__ = function.__kwdefaults__
    return relocated


def _public_code_fingerprint() -> Any:
    spec = importlib.util.find_spec("wind_forecast.application.code_identity")
    assert spec is not None
    return getattr(
        importlib.import_module("wind_forecast.application.code_identity"),
        "code_fingerprint",
        None,
    )


def test_service_identity_ignores_source_positions_in_nested_code(monkeypatch):
    original = ForecastModel.predict
    assert any(isinstance(value, CodeType) for value in original.__code__.co_consts)
    before = Application._predictor_code_identity()

    monkeypatch.setattr(ForecastModel, "predict", _relocated_function(original))

    after = Application._predictor_code_identity()

    assert after == before


def test_code_identity_changes_for_constants_and_bytecode():
    def one():
        return 1

    def two():
        return 2

    def add_zero(value):
        return value + 0

    def multiply_zero(value):
        return value * 0

    assert one.__code__.co_code == two.__code__.co_code
    assert add_zero.__code__.co_code != multiply_zero.__code__.co_code
    fingerprint = _public_code_fingerprint()

    assert callable(fingerprint)
    assert fingerprint(one.__code__) != fingerprint(two.__code__)
    assert fingerprint(add_zero.__code__) != fingerprint(multiply_zero.__code__)


def test_public_code_fingerprint_preserves_agent_fingerprint_compatibility():
    spec = importlib.util.find_spec("wind_forecast.application.code_identity")
    assert spec is not None
    identity = importlib.import_module("wind_forecast.application.code_identity")
    code = compile("lambda value: value + 1", "<identity-fixture>", "eval").co_consts[0]

    assert agent._code_fingerprint is identity.code_fingerprint
    assert agent._code_identity is identity._code_identity
    assert agent._code_fingerprint(code) == identity.code_fingerprint(code)


def test_code_fingerprint_is_stable_after_interpreter_warmup():
    def repeatedly_add(value: int) -> int:
        total = 0
        for _ in range(8):
            total += value
        return total

    fingerprint = _public_code_fingerprint()
    assert callable(fingerprint)
    before = fingerprint(repeatedly_add.__code__)

    for _ in range(20_000):
        assert repeatedly_add(3) == 24

    assert fingerprint(repeatedly_add.__code__) == before


def test_unchanged_refresh_reuses_run_after_code_moves(tmp_path, monkeypatch):
    from tests.test_e2e import _settings
    from wind_forecast.contracts import parse_request

    app = Application(
        _settings(tmp_path), offline=True, model_parameters={"iterations": 2}
    )
    request = parse_request("2026-02-01T00:00:00+05:00", 24, "demo")
    first = app.run(request)
    original = Application.train

    monkeypatch.setattr(Application, "train", _relocated_function(original))

    refreshed = app.run(request, refresh=True)

    assert refreshed.reused is True
    assert refreshed.run_id == first.run_id
