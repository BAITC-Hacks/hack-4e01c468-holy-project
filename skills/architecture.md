# Architecture Skill — Maintainable Python/ML Project

## Role

You are a Senior Software Architect responsible for keeping this project clean, modular, readable, testable, and easy to extend.

When implementing new functionality, refactoring existing code, or creating files, follow the architecture described in this skill.

The project is a wind-power forecasting platform containing:

* ML forecasting
* weather integrations
* data processing
* model training
* backtesting and evaluation
* AI agents
* API / CLI
* dashboard/frontend
* generated artifacts and reports

The main architectural approach is:

**Modular Monolith + Clean Architecture + Feature-oriented organization.**

Do NOT introduce unnecessary microservices or infrastructure unless the project actually requires them.

---

# 1. Core Principles

Always optimize for:

1. Readability
2. Clear responsibility
3. Low coupling
4. High cohesion
5. Testability
6. Replaceability
7. Easy future extension

Prefer simple explicit code over clever abstractions.

A developer should be able to understand what a module does from its path and filename.

Avoid giant files.

Avoid circular dependencies.

Avoid mixing business logic, ML logic, infrastructure, UI, and orchestration in the same module.

---

# 2. Target Project Structure

```text
src/
└── wind_forecast/

    domain/
        entities/
        value_objects/
        exceptions.py

    application/
        forecasting/
        training/
        data_quality/
        ports/

    ml/
        features/
        models/
        training/
        inference/
        evaluation/

    infrastructure/
        weather/
        persistence/
        datasets/
        llm/

    agents/
        tools/
        prompts/
        forecast_agent.py

    presentation/
        api/
            routes/
        cli/
            commands/
        dashboard/

    config/
        settings.py
        logging.py

    bootstrap.py
```

Other top-level directories:

```text
frontend/
tests/
data/
artifacts/
scripts/
docs/
```

---

# 3. Dependency Direction

Dependencies must generally flow inward.

```text
Presentation
     │
     ▼
Application
     │
     ├──────────► Domain
     │
     └──────────► ML
     ▲
     │ ports/interfaces
     │
Infrastructure

Agents ─────────► Application
```

The most important rule:

> Infrastructure depends on application contracts. Application must not depend on infrastructure implementations.

For example, application code may depend on:

```python
WeatherProvider
ModelRepository
ArtifactStore
LLMProvider
```

but must NOT directly depend on:

```python
OpenMeteoClient
CatBoost filesystem paths
requests.get(...)
specific API credentials
```

---

# 4. Domain Layer

Location:

```text
src/wind_forecast/domain/
```

Contains pure domain concepts.

Examples:

```text
Forecast
ForecastPoint
Turbine
WeatherSnapshot
ModelMetadata
Power
ForecastHorizon
TimeRange
```

Domain objects must not know about:

* HTTP
* Astro
* FastAPI
* CLI
* filesystem
* CSV
* OpenMeteo
* CatBoost serialization
* environment variables
* LLM APIs

Domain code should be deterministic whenever possible.

---

# 5. Application Layer

Location:

```text
src/wind_forecast/application/
```

Application contains **use cases**.

Examples:

```text
GenerateForecast
TrainModel
BacktestModel
AnalyzeDataQuality
GetForecast
GetModelMetrics
```

Prefer use-case classes/functions with explicit inputs and outputs.

Example:

```python
class GenerateForecast:
    def __init__(
        self,
        weather: WeatherProvider,
        models: ModelRepository,
    ):
        self.weather = weather
        self.models = models

    def execute(
        self,
        request: ForecastRequest,
    ) -> Forecast:
        ...
```

Application code coordinates operations.

It should not contain low-level implementation details.

---

# 6. Ports

Interfaces to external systems belong in:

```text
application/ports/
```

Examples:

```text
weather_provider.py
model_repository.py
artifact_store.py
llm.py
```

Use `Protocol` or small abstract interfaces when useful.

Example:

```python
from typing import Protocol

class WeatherProvider(Protocol):
    def get_forecast(
        self,
        start,
        horizon,
    ) -> WeatherForecast:
        ...
```

Do not create interfaces for everything.

Create a port when there is a meaningful external boundary or replaceable implementation.

---

# 7. ML Layer

All machine-learning-specific logic belongs under:

```text
src/wind_forecast/ml/
```

Structure:

```text
ml/
├── features/
├── models/
├── training/
├── inference/
└── evaluation/
```

## Features

Feature engineering belongs in:

```text
ml/features/
```

Separate feature categories when appropriate:

```text
temporal.py
weather.py
turbine.py
builder.py
```

Do not mix feature engineering with API/data downloading.

---

## Models

Model implementations belong in:

```text
ml/models/
```

Examples:

```text
base.py
catboost.py
lightgbm.py
ensemble.py
registry.py
```

Adding another forecasting algorithm should not require rewriting application code.

---

## Training

Training pipelines belong in:

```text
ml/training/
```

Examples:

```text
trainer.py
pipeline.py
```

Training and inference should remain conceptually separate.

---

## Inference

Prediction logic belongs in:

```text
ml/inference/
```

Example:

```text
predictor.py
```

---

## Evaluation

Metrics and backtesting belong in:

```text
ml/evaluation/
```

Examples:

```text
metrics.py
backtest.py
validation.py
```

---

# 8. Infrastructure Layer

Location:

```text
src/wind_forecast/infrastructure/
```

Infrastructure implements external integrations.

Examples:

```text
infrastructure/
├── weather/
│   ├── open_meteo.py
│   └── synthetic.py
├── persistence/
│   ├── filesystem_models.py
│   └── artifact_store.py
├── datasets/
│   ├── csv_loader.py
│   └── preprocessing.py
└── llm/
    └── client.py
```

Infrastructure may know about:

* HTTP
* APIs
* filesystem
* CSV
* JSON
* environment variables
* model serialization
* external libraries

Application should not know those details.

---

# 9. Agent Architecture

Agents belong under:

```text
src/wind_forecast/agents/
```

IMPORTANT:

**The AI agent must NOT become the core of the application.**

Never design:

```text
UI
 ↓
Agent
 ↓
Everything
```

Prefer:

```text
             Agent
               │
               ▼
UI ──► Application Services ◄── CLI
               │
               ▼
              ML
```

Agents should call application use cases through tools.

Example:

```python
class ForecastAgentTool:
    def __init__(
        self,
        generate_forecast: GenerateForecast,
    ):
        self.generate_forecast = generate_forecast

    def execute(self, horizon: int):
        return self.generate_forecast.execute(
            ForecastRequest(horizon=horizon)
        )
```

The forecasting system must continue working even if the LLM/agent layer is completely removed.

---

# 10. Presentation Layer

User-facing/backend entry points belong under:

```text
presentation/
```

Possible interfaces:

```text
presentation/
├── api/
├── cli/
└── dashboard/
```

Presentation code should:

1. parse input
2. validate transport-level data
3. call application use cases
4. serialize output

Presentation code should NOT contain forecasting algorithms or business logic.

---

# 11. Dependency Wiring

Concrete implementations should be assembled in:

```text
bootstrap.py
```

Example:

```python
weather_provider = OpenMeteoWeatherProvider(settings)

model_repository = FileSystemModelRepository(
    settings.model_directory
)

generate_forecast = GenerateForecast(
    weather=weather_provider,
    models=model_repository,
)
```

Keep dependency construction outside business logic.

---

# 12. Frontend Architecture

The Astro/Lumen frontend should use feature-oriented organization.

Preferred structure:

```text
frontend/src/

components/
    AppShell.astro
    MetricCard.astro
    StatusBadge.astro
    SectionHeader.astro

features/
    forecast/
        ForecastChart.astro
        ForecastSummary.astro
        ForecastTable.astro
        api.ts

    backtest/
        BacktestChart.astro
        MetricsPanel.astro
        api.ts

    data-quality/
        QualityPanel.astro
        QualityMetric.astro

    agent-trace/
        AgentTrace.astro
        AgentStep.astro

layouts/
    DashboardLayout.astro

pages/
    index.astro

lib/
    api.ts
    format.ts
    types.ts

styles/
    global.css
    tokens.css
```

Pages should compose features instead of implementing everything themselves.

Good:

```astro
<DashboardLayout>
    <ForecastSummary forecast={forecast} />
    <ForecastChart forecast={forecast} />
    <MetricsPanel metrics={metrics} />
    <QualityPanel quality={quality} />
    <AgentTrace steps={steps} />
</DashboardLayout>
```

Avoid giant `index.astro` files.

---

# 13. Tests

Tests should mirror architectural boundaries.

```text
tests/
├── unit/
│   ├── domain/
│   ├── application/
│   └── ml/
├── integration/
│   ├── weather/
│   ├── artifacts/
│   └── models/
├── contract/
├── e2e/
├── acceptance/
└── fixtures/
```

Use unit tests for pure logic.

Use integration tests for external boundaries.

Use E2E tests only for important complete workflows.

Do not test implementation details unnecessarily.

---

# 14. Generated Files

Generated files are NOT source code.

Store them under:

```text
artifacts/
```

Examples:

```text
artifacts/
├── models/
├── runs/
├── reports/
└── ui-review/
```

Datasets should live under:

```text
data/
├── raw/
├── processed/
└── fixtures/
```

Do not scatter generated `.csv`, `.json`, `.cbm`, screenshots, reports, or model files across source directories.

---

# 15. Configuration

Configuration belongs under:

```text
config/
```

Application code must not contain hardcoded:

* API keys
* paths
* URLs
* model directories
* environment-specific settings

Never commit secrets.

Never place secrets in source code.

Use environment variables and centralized settings.

---

# 16. Naming

Prefer descriptive names.

Good:

```text
generate_forecast.py
weather_provider.py
model_repository.py
backtest.py
forecast_agent.py
```

Avoid vague names such as:

```text
utils.py
helpers.py
common.py
manager.py
stuff.py
misc.py
```

If a `utils.py` begins growing, split it by responsibility.

---

# 17. File Size

Prefer small focused modules.

As a guideline:

* < 200 lines: ideal
* 200–400 lines: acceptable when cohesive
* > 400 lines: inspect whether responsibilities should be split
* > 700 lines: usually refactor

Do not split files merely to satisfy a line-count rule.

Cohesion matters more than raw size.

---

# 18. Function Design

Functions should do one understandable thing.

Prefer:

```python
weather = weather_provider.get_forecast(...)
features = feature_builder.build(weather)
prediction = predictor.predict(features)
```

instead of:

```python
result = process_everything(...)
```

Use explicit parameters.

Avoid hidden global state.

---

# 19. DTOs and Contracts

Boundary data should have explicit schemas.

Use typed DTOs/dataclasses/Pydantic models where appropriate.

Example:

```python
@dataclass(frozen=True)
class ForecastRequest:
    horizon_hours: int
    turbine_id: str
```

Do not pass unstructured dictionaries through the entire system.

Dictionaries are acceptable at serialization boundaries, but convert them into typed structures before entering core logic.

---

# 20. Error Handling

Use meaningful exceptions.

Examples:

```text
WeatherUnavailableError
ModelNotFoundError
InvalidForecastHorizonError
DataQualityError
```

Avoid:

```python
raise Exception("error")
```

Infrastructure errors should be translated into application/domain-level errors when crossing architectural boundaries.

---

# 21. Logging

Use structured logging.

Important operations should expose useful context:

```text
run_id
model_id
turbine_id
forecast_horizon
provider
duration
```

Do not use `print()` for production diagnostics.

---

# 22. Adding New Features

Before implementing a feature, determine which layer owns it.

Ask:

### Is this a business concept?

Put it in:

```text
domain/
```

### Is this something the system does?

Put it in:

```text
application/
```

### Is this forecasting/ML logic?

Put it in:

```text
ml/
```

### Does it communicate with something external?

Put it in:

```text
infrastructure/
```

### Is it an LLM/agent capability?

Put it in:

```text
agents/
```

### Is it HTTP/CLI/UI?

Put it in:

```text
presentation/
```

---

# 23. Example: Adding Another Weather Provider

Do NOT modify forecasting logic.

Implement:

```text
infrastructure/weather/ecmwf.py
```

against:

```python
WeatherProvider
```

Then change dependency wiring in:

```text
bootstrap.py
```

No application-level rewrite should be necessary.

---

# 24. Example: Adding LightGBM

Add:

```text
ml/models/lightgbm.py
```

Register it through the model registry/factory.

Do not add:

```python
if model_type == "catboost":
    ...
elif model_type == "lightgbm":
    ...
```

throughout unrelated modules.

Keep model-specific behavior behind the model abstraction.

---

# 25. Example: Adding a New Dashboard Feature

For something like turbine health:

```text
frontend/src/features/turbine-health/
├── TurbineHealthPanel.astro
├── TurbineStatus.astro
├── api.ts
└── types.ts
```

Do not put all turbine-health code directly inside:

```text
pages/index.astro
```

---

# 26. Avoid Premature Complexity

Do NOT introduce without a concrete need:

* microservices
* Kafka
* RabbitMQ
* Redis
* Celery
* Kubernetes
* service mesh
* event sourcing
* CQRS
* database repositories for every object
* abstract factories everywhere
* unnecessary dependency injection frameworks

The architecture should make these additions possible later without requiring them today.

---

# 27. Refactoring Existing Project

Current modules should gradually migrate approximately as follows:

```text
data.py
→ infrastructure/datasets/

features.py
→ ml/features/

models.py
→ ml/models/
→ ml/training/

evaluation.py
→ ml/evaluation/

weather.py
→ infrastructure/weather/

artifacts.py
→ infrastructure/persistence/

contracts.py
→ domain/
→ application DTOs

service.py
→ application/forecasting/

agent.py
→ agents/forecast_agent.py

llm.py
→ infrastructure/llm/

cli.py
→ presentation/cli/

ui/
→ presentation/dashboard/
or frontend/
```

Refactor incrementally.

Do NOT perform a massive rewrite unless explicitly requested.

Existing behavior and tests must remain working during migration.

---

# 28. Change Workflow

When asked to implement a new feature:

1. Inspect existing architecture.
2. Identify the correct architectural layer.
3. Reuse existing abstractions where appropriate.
4. Create a new abstraction only when it represents a real boundary.
5. Keep business logic independent from infrastructure.
6. Add/update tests.
7. Run relevant tests.
8. Check imports and dependency direction.
9. Remove obsolete code only after confirming it is unused.
10. Keep generated artifacts outside source code.

---

# 29. Refactoring Rules

When refactoring:

* preserve behavior
* preserve public contracts unless change is intentional
* prefer small incremental changes
* keep tests green
* avoid unrelated rewrites
* remove duplication when ownership is clear
* do not create abstractions merely to reduce line count

If architecture and speed conflict during a hackathon, choose the simplest implementation that does not create obvious long-term coupling.

---

# 30. Definition of Done

A feature is complete when:

* responsibility is in the correct layer
* naming is clear
* types/contracts are explicit
* external dependencies are isolated
* core logic can be tested independently
* relevant tests exist
* tests pass
* no secrets are hardcoded
* generated files are stored correctly
* no unnecessary abstraction was introduced
* documentation is updated when architecture or public behavior changes

---

# 31. Final Architectural Rule

When deciding where code belongs, use this mental model:

```text
DOMAIN
What exists?

APPLICATION
What can the system do?

ML
How do we forecast?

INFRASTRUCTURE
How do we communicate with the outside world?

AGENTS
How can AI operate the application?

PRESENTATION
How do humans and programs access it?

FRONTEND
What does the user see?
```

If a module answers more than one of these questions, inspect whether it should be split.

The goal is not maximum abstraction.

The goal is:

> A codebase where a new developer can locate, understand, modify, test, and extend a feature without understanding the entire system.
