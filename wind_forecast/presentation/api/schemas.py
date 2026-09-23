"""Strict request schemas for the local forecast API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ForecastJobInput(BaseModel):
    """Client-controlled fields for one forecast request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    origin: str = Field(min_length=1, max_length=64)
    horizon: Literal[24, 48]
    refresh: bool = False
