# Composition Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax. User selected Luna xhigh workers with Astra review, shared worktree, no commits.

**Goal:** Implement only the user-approved first architecture batch: explicit runtime composition and two meaningful external ports without changing application behavior.

**Architecture:** Keep `service.Application` as the public compatibility facade. Move concrete constructor choices into `bootstrap.build_runtime`; relocate its small demo-weather adapters and deterministic analyzer into their owning layers so bootstrap never imports service. Existing workflow, storage, ML and presentation modules remain canonical and unchanged.

**Tech Stack:** Existing Python/dataclasses/typing.Protocol, pandas, pytest. No new dependencies.

**Spec:** `docs/architecture/2026-09-23-modular-redesign.md`, batch 1; `skills/architecture.md`. User approved first batch now, remaining migration gradually.

## Global Constraints

- Preserve Application constructor/method signatures, CLI commands, dashboard behavior, schemas and point-in-time rules.
- Keep source/model/weather identities and serialized artifacts unchanged; no new training or weather request during construction. Review ruling: legacy position-sensitive implementation-code fingerprints may invalidate once when replaced by stable semantic fingerprints; never force reuse across unknown old code. Subsequent line-only moves must not invalidate runs. Existing artifacts remain immutable.
- Do not migrate to src/, introduce an API, change the UI runtime, or execute architecture batches 2–7.
- Do not edit service.py until integration-fix owner releases it; inspect its final handoff first.
- No commits, branches, destructive operations, dependency installs or edits to user reference files.

## Review Focus

1. Explicit injected weather and analyzer remain selected over defaults (Task 1).
2. Offline mode never constructs/calls OpenAI even with a configured key (Task 1).
3. Conflicting offline and fixture options still fail; fixed fixtures retain exact timestamp semantics (Task 1).
4. Runtime objects are independent per Application instance, with correct configured directories (Task 1).
5. No bootstrap/service circular import; CLI entrypoint, caches and artifact hashes remain compatible (Task 2).

## File ownership

- Create `wind_forecast/application/__init__.py`, `application/ports.py`: two external contracts actually consumed by runtime wiring.
- Create `wind_forecast/application/forecasting/__init__.py`, `application/forecasting/fallback_policy.py`: current deterministic analyzer, moved unchanged.
- Create `wind_forecast/infrastructure/__init__.py`, `infrastructure/weather/__init__.py`, `infrastructure/weather/demo.py`: current synthetic/fixed-fixture adapters, moved unchanged.
- Create `wind_forecast/bootstrap.py`: typed RuntimeComponents and build_runtime, imports adapters but never service.
- Modify `wind_forecast/service.py`: constructor delegates component construction, preserves facade state and methods; remove now-unused concrete imports/helper definitions.
- Create `tests/test_bootstrap.py`; update architectural design status and role report after verification. Leave app.py/CLI/UI and unrelated modules unchanged.

### Task 1: Compose runtime through explicit ports

**Consumes:** `Settings`, existing `RunRequest`, `WeatherSnapshot`, `Decision`, `DataPipeline`, `FeaturePipeline`, `ArtifactStore`, `WeatherProvider`, `OpenAIAnalyzer`.

**Produces:** `build_runtime(settings, *, offline=False, weather_fixture=None, weather_provider=None, analyzer=None) -> RuntimeComponents`, with fields `weather_provider`, `analyzer`, `data_pipeline`, `feature_pipeline`, `store`.

- [ ] Write behavior tests first in tests/test_bootstrap.py. Use tmp_path settings with empty key and project fixture path where required. For injection, assert object identity; for offline, fetch a real synthetic snapshot and assert demo provenance and 96 rows; invoke analyzer on an allowed action and verify its actual Decision. Exercise conflicting options and two independent runtime graphs.

```python
def test_explicit_injections_win(tmp_path):
    settings = Settings(root_dir=tmp_path, cache_dir=tmp_path / 'cache',
                        model_dir=tmp_path / 'models', run_dir=tmp_path / 'runs',
                        openai_api_key='', mode='demo')
    weather = object()
    analyzer = object()
    runtime = build_runtime(settings, weather_provider=weather, analyzer=analyzer)
    assert runtime.weather_provider is weather
    assert runtime.analyzer is analyzer

def test_conflicting_demo_sources_rejected(tmp_path):
    with pytest.raises(ValueError, match='cannot be used together'):
        build_runtime(Settings(), offline=True, weather_fixture=tmp_path / 'fixture.json')
```

- [ ] Run `.venv/bin/python -m pytest tests/test_bootstrap.py -q`; confirm missing build_runtime fails before implementation.
- [ ] Declare only the two ports below. Protocol ellipses are declarations, not unfinished implementation. Keep contract-bearing pandas objects as existing application DTOs, not a newly claimed pure domain.

```python
from typing import Any, Protocol
from wind_forecast.contracts import Decision, RunRequest, WeatherSnapshot

class WeatherSource(Protocol):
    def fetch(self, request: RunRequest, refresh: bool = False) -> WeatherSnapshot: ...

class DiagnosticAnalyzer(Protocol):
    def __call__(self, diagnostics: dict[str, Any], allowed: set[str]) -> Decision: ...
```

- [ ] Move the final integration implementation's `_SyntheticWeatherProvider` and `_FixtureWeatherProvider` to infrastructure/weather/demo.py and `_DeterministicAnalyzer` to application/forecasting/fallback_policy.py, without changing their algorithms. Rename private classes to descriptive public adapter names in the destination; check repository imports before removing old definitions. Compatibility aliases in service are permitted only if a real caller requires them. Never duplicate active implementations or import service from bootstrap.
- [ ] Implement frozen RuntimeComponents dataclass in bootstrap.py. Instantiate the injected provider when non-None; otherwise select explicit synthetic, fixed fixture, or actual weather provider exactly as the existing constructor does. Select the injected analyzer when non-None; otherwise deterministic for offline/absent key, OpenAIAnalyzer for configured live mode. Build fresh DataPipeline, FeaturePipeline and ArtifactStore(settings.run_dir) per runtime. Factory construction must not fetch weather or fit models.
- [ ] Add tests forbidding OpenAIAnalyzer construction in offline mode via a failing sentinel; malformed fixture behavior; distinct store/pipeline instances; store.root uses supplied run directory. Run the focused suite to green.

```python
def test_offline_graph_has_real_demo_behavior_without_openai(tmp_path, monkeypatch):
    import wind_forecast.bootstrap as wiring
    def forbidden_client(*args, **kwargs):
        raise AssertionError('offline graph constructed OpenAI')
    monkeypatch.setattr(wiring, 'OpenAIAnalyzer', forbidden_client)
    settings = Settings(root_dir=tmp_path, run_dir=tmp_path / 'runs',
                        openai_api_key='test-sentinel', mode='demo')
    runtime = wiring.build_runtime(settings, offline=True)
    request = parse_request('2026-02-01T00:00:00+05:00', 48, 'demo')
    snapshot = runtime.weather_provider.fetch(request)
    assert len(snapshot.rows) == 96
    assert snapshot.provenance['competition_valid'] is False
    assert runtime.analyzer({}, {'continue'}).action == 'continue'

def test_runtime_instances_do_not_share_mutable_components(tmp_path):
    settings = Settings(run_dir=tmp_path / 'runs', openai_api_key='', mode='demo')
    first = build_runtime(settings, offline=True)
    second = build_runtime(settings, offline=True)
    assert first.store is not second.store
    assert first.data_pipeline is not second.data_pipeline
    assert first.feature_pipeline is not second.feature_pipeline
    assert first.store.root == (tmp_path / 'runs').resolve()

def test_malformed_fixed_fixture_is_rejected(tmp_path):
    path = tmp_path / 'broken.json'
    path.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError, match='fixture'):
        build_runtime(Settings(openai_api_key=''), weather_fixture=path)
```

### Task 2: Delegate facade wiring and verify public behavior

**Consumes:** Task 1 RuntimeComponents. **Produces:** same Application/CLI/dashboard public behavior with construction in bootstrap.

- [ ] Add test constructing real Application with injected objects, then check they remain its weather_provider/analyzer; construct offline Application and call the existing public run API on the small E2E fixture.
- [ ] Replace only the concrete construction section of Application.__init__ with:

```python
from wind_forecast.bootstrap import build_runtime

runtime = build_runtime(settings, offline=self.offline,
                        weather_fixture=self.weather_fixture,
                        weather_provider=weather_provider, analyzer=analyzer)
self.weather_provider = runtime.weather_provider
self.analyzer = runtime.analyzer
self.data_pipeline = runtime.data_pipeline
self.feature_pipeline = runtime.feature_pipeline
self.store = runtime.store
```

- [ ] Retain pure constructor settings/model-parameter merge and all prepared-history/cache/simulation fields. Remove only provably unused concrete imports and moved helper bodies. No run, training, prediction, backtest, cache or serialization rewrite in this batch.
- [ ] Add import smoke for bootstrap then service and service then bootstrap in fresh subprocesses; no circular import in either order. Port modules must import no infrastructure, OpenAI SDK or HTTP client; inspect imports and add a small dependency-boundary check for these new modules.

```python
@pytest.mark.parametrize('modules', [
    ('wind_forecast.bootstrap', 'wind_forecast.service'),
    ('wind_forecast.service', 'wind_forecast.bootstrap'),
])
def test_composition_import_orders(modules):
    import subprocess
    import sys
    code = '\n'.join(f'import {name}' for name in modules)
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

def test_ports_have_no_outward_imports():
    import ast
    import inspect
    import wind_forecast.application.ports as ports
    tree = ast.parse(inspect.getsource(ports))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or '')
    forbidden = ('wind_forecast.infrastructure', 'wind_forecast.bootstrap',
                 'wind_forecast.service', 'requests', 'openai')
    assert not any(name == prefix or name.startswith(prefix + '.')
                   for name in names for prefix in forbidden)
```
- [ ] Run `.venv/bin/python -m pytest tests/test_bootstrap.py tests/test_cli.py tests/test_e2e.py tests/test_acceptance.py tests/test_app_entry.py -q`, then the complete offline suite. Run CLI --help, git diff --check. If a named entry test has not been created by integration-fix, inspect its handoff and use the actual entrypoint test module, without skipping equivalent coverage.
- [ ] Root performs a saved-model demo run and unchanged refresh: grid, model identity, provenance and persisted artifact integrity must still pass. Record changed files, exact tests, remaining limitations in `.hackflow/reports/architecture-batch1.md` and mark only batch 1 completed in architectural design.

## Self-review

This implements the approved composition-root/ports batch only. Two helper relocations prevent a reverse bootstrap→service dependency and keep adapter logic out of the factory. No broader module migration or new product scope is included. Every review focus is covered above; retain current signatures from the final integration-fix handoff when applying constructor extraction.
