"""Command line interface for preparing and forecasting wind power."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Sequence

from wind_forecast.config import Settings
from wind_forecast.contracts import parse_request
from wind_forecast.service import (
    Application,
    DEFAULT_BACKTEST_END,
    DEFAULT_BACKTEST_START,
    DEFAULT_CALIBRATION_END,
    DEFAULT_CALIBRATION_START,
    DEFAULT_TRAIN_END,
    DEFAULT_TRAIN_START,
)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return number


def _add_shared_options(parser: argparse.ArgumentParser, *, subcommand: bool) -> None:
    absent = argparse.SUPPRESS if subcommand else None
    parser.add_argument("--mode", choices=("demo", "competition"), default=absent)
    parser.add_argument("--data-dir", type=Path, default=absent)
    parser.add_argument("--cache-dir", type=Path, default=absent)
    parser.add_argument("--model-dir", type=Path, default=absent)
    parser.add_argument("--run-dir", type=Path, default=absent)
    parser.add_argument("--weather-fixture", type=Path, default=absent)
    parser.add_argument("--offline", action="store_true", default=argparse.SUPPRESS if subcommand else False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wind-forecast",
        description="Prepare data, train and evaluate models, then create auditable forecasts.",
    )
    _add_shared_options(parser, subcommand=False)
    commands = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("prepare", "prepare and cache UTC hourly turbine history"),
        ("train", "build historical training/calibration rows and save a model"),
        ("backtest", "run rolling 48-hour historical evaluation"),
        ("run", "produce one 24- or 48-hour forecast"),
        ("simulate", "run inclusive daily forecasts and write an outcome index"),
    ):
        command = commands.add_parser(name, help=help_text)
        _add_shared_options(command, subcommand=True)
        if name == "run":
            command.add_argument("--origin", required=True, help="zoned ISO-8601 hour boundary")
            command.add_argument("--horizon", type=int, choices=(24, 48), default=48)
            command.add_argument("--refresh", action="store_true", help="bypass weather caches")
        elif name == "train":
            command.add_argument("--train-start", type=_iso_date, default=DEFAULT_TRAIN_START)
            command.add_argument("--train-end", type=_iso_date, default=DEFAULT_TRAIN_END)
            command.add_argument("--calibration-start", type=_iso_date, default=DEFAULT_CALIBRATION_START)
            command.add_argument("--calibration-end", type=_iso_date, default=DEFAULT_CALIBRATION_END)
            command.add_argument("--iterations", type=_positive_int)
            command.add_argument("--refresh", action="store_true")
        elif name == "backtest":
            command.add_argument("--start", type=_iso_date, default=DEFAULT_BACKTEST_START)
            command.add_argument("--end", type=_iso_date, default=DEFAULT_BACKTEST_END)
            command.add_argument("--history-start", type=_iso_date, default=DEFAULT_TRAIN_START)
            command.add_argument("--iterations", type=_positive_int)
            command.add_argument("--calibration-days", type=_positive_int, default=15)
            command.add_argument("--refresh", action="store_true")
        elif name == "simulate":
            command.add_argument("--start", type=_iso_date, required=True)
            command.add_argument("--end", type=_iso_date, required=True)
            command.add_argument("--refresh", action="store_true")
    return parser


def _application(args: argparse.Namespace, env_settings: Settings | None = None) -> Application:
    settings = env_settings or Settings.from_env()
    changes = {"mode": args.mode or settings.mode}
    for name in ("data_dir", "cache_dir", "model_dir", "run_dir"):
        value = getattr(args, name, None)
        if value is not None:
            setattr_placeholder = value.expanduser()
            changes[name] = setattr_placeholder if setattr_placeholder.is_absolute() else settings.root_dir / setattr_placeholder
    settings = replace(settings, **changes)
    fixture = getattr(args, "weather_fixture", None)
    if getattr(args, "offline", False) and fixture is not None:
        raise ValueError("--offline and --weather-fixture cannot be used together")
    if getattr(args, "offline", False) and settings.mode != "demo":
        raise _InvalidArguments("offline weather requires --mode demo")
    if fixture is not None and settings.mode != "demo":
        raise _InvalidArguments("--weather-fixture requires --mode demo")
    if fixture is not None and args.command != "run":
        raise _InvalidArguments("--weather-fixture is supported for run only")
    return Application(settings, offline=getattr(args, "offline", False), weather_fixture=fixture)


def _execute(args: argparse.Namespace) -> int:
    app = _application(args)
    mode = args.mode or app.settings.mode
    if args.command == "prepare":
        path = app.prepare()
        frame = app._load_history()
        print(f"prepared_history={path} rows={len(frame)} source_timezone_policy=fixed_UTC+05:00")
        return 0
    if args.command == "train":
        prefix = app.train(
            mode=mode,
            train_start=args.train_start,
            train_end=args.train_end,
            calibration_start=args.calibration_start,
            calibration_end=args.calibration_end,
            iterations=args.iterations,
            refresh=args.refresh,
        )
        print(f"status=trained mode={mode} model={prefix}")
        return 0
    if args.command == "backtest":
        metrics_path = app.backtest(
            mode=mode,
            start=args.start,
            end=args.end,
            history_start=args.history_start,
            iterations=args.iterations,
            calibration_days=args.calibration_days,
            refresh=args.refresh,
        )
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        coverage = metrics.get("coverage", {})
        print(
            f"status={metrics.get('metric_status')} mode={mode} "
            f"evaluated_origins={coverage.get('evaluated_origins', 0)} metrics={metrics_path}"
        )
        return 0
    if args.command == "run":
        try:
            request = parse_request(args.origin, args.horizon, mode)
        except ValueError as exc:
            raise _InvalidArguments(str(exc)) from exc
        result = app.run(request, refresh=args.refresh)
        manifest = json.loads((result.directory / "manifest.json").read_text(encoding="utf-8"))
        print(
            f"status={result.status} mode={mode} competition_valid={str(manifest['competition_valid']).lower()} "
            f"run_id={result.run_id} directory={result.directory}"
        )
        return 1 if result.status == "failed" else 0
    if args.command == "simulate":
        results = app.simulate(args.start, args.end, mode=mode, refresh=args.refresh)
        if app.last_simulation_index is None:
            print("error: simulation index was not written", file=sys.stderr)
            return 1
        index = json.loads(app.last_simulation_index.read_text(encoding="utf-8"))
        failed = int(index["failed_origins"])
        print(
            f"status={'failed' if failed else 'complete'} mode={mode} "
            f"origins={len(index['origins'])} results={len(results)} failed={failed} "
            f"index={app.last_simulation_index}"
        )
        return 1 if failed else 0
    raise AssertionError(f"unknown command: {args.command}")


class _InvalidArguments(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _execute(args)
    except _InvalidArguments as exc:
        parser.error(str(exc))
    except Exception as exc:
        message = str(exc).strip()
        if not message or "api_key" in message.lower() or "authorization" in message.lower():
            message = type(exc).__name__
        print(f"error: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
