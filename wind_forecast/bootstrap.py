"""Construct the concrete runtime graph used by the application facade."""

from dataclasses import dataclass
from pathlib import Path

from wind_forecast.application.forecasting.fallback_policy import DeterministicAnalyzer
from wind_forecast.application.ports import DiagnosticAnalyzer, WeatherSource
from wind_forecast.artifacts import ArtifactStore
from wind_forecast.config import Settings
from wind_forecast.data import DataPipeline
from wind_forecast.features import FeaturePipeline
from wind_forecast.infrastructure.weather.demo import (
    FixtureWeatherProvider,
    SyntheticWeatherProvider,
)
from wind_forecast.llm import OpenAIAnalyzer
from wind_forecast.weather import WeatherProvider


@dataclass(frozen=True)
class RuntimeComponents:
    """Fresh adapters and pipelines for one Application instance."""

    weather_provider: WeatherSource
    analyzer: DiagnosticAnalyzer
    data_pipeline: DataPipeline
    feature_pipeline: FeaturePipeline
    store: ArtifactStore


def build_runtime(
    settings: Settings,
    *,
    offline: bool = False,
    weather_fixture: Path | None = None,
    weather_provider: WeatherSource | None = None,
    analyzer: DiagnosticAnalyzer | None = None,
) -> RuntimeComponents:
    """Select runtime adapters and create fresh stateful pipelines and storage."""
    fixture_path = (
        Path(weather_fixture).expanduser().resolve()
        if weather_fixture is not None
        else None
    )
    if offline and fixture_path is not None:
        raise ValueError("--offline and --weather-fixture cannot be used together")

    if weather_provider is not None:
        selected_weather = weather_provider
    elif offline:
        selected_weather = SyntheticWeatherProvider()
    elif fixture_path is not None:
        selected_weather = FixtureWeatherProvider(fixture_path)
    elif settings.weather_provider == "noaa_gfs":
        from wind_forecast.infrastructure.weather.noaa_gfs import (
            NoaaGfsWeatherProvider,
        )

        selected_weather = NoaaGfsWeatherProvider(settings.cache_dir)
    else:
        selected_weather = WeatherProvider(settings.cache_dir)

    if analyzer is not None:
        selected_analyzer = analyzer
    elif offline or not settings.openai_api_key:
        selected_analyzer = DeterministicAnalyzer()
    else:
        selected_analyzer = OpenAIAnalyzer(settings)

    return RuntimeComponents(
        weather_provider=selected_weather,
        analyzer=selected_analyzer,
        data_pipeline=DataPipeline(),
        feature_pipeline=FeaturePipeline(),
        store=ArtifactStore(settings.run_dir),
    )
