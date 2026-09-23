"""Run the loopback-only HTTP API."""

from __future__ import annotations

import argparse

import uvicorn

from wind_forecast.presentation.api import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local wind forecast API.")
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, choices=(8000, 4321), default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
