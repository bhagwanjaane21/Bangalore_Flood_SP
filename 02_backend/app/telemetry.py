"""Validated input and shared timestamp serialization for telemetry."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .alert_engine import alert_snapshot


def utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class TelemetryReading(BaseModel):
    node_id: str = Field(strict=True, min_length=1, max_length=64)
    water_depth_m: float = Field(strict=True, ge=0, allow_inf_nan=False)
    rate_of_rise_cm_min: float | None = Field(default=None, strict=True, allow_inf_nan=False)
    battery_voltage: float | None = Field(default=None, strict=True, ge=0, allow_inf_nan=False)
    observed_at: datetime | None = None
    source: Literal["unknown", "simulated", "hardware", "manual"] = "unknown"

    @field_validator("observed_at", mode="before")
    @classmethod
    def require_datetime_input(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("observed_at must be an ISO 8601 datetime with a timezone")
        return value

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("observed_at must include a timezone offset or Z")
            return value.astimezone(timezone.utc)
        return value


class TelemetryConflict(ValueError):
    """An observation conflicts with an existing/newer observation."""


def public_reading(row: dict) -> dict:
    return {
        **alert_snapshot(row),
        "reading_id": row["id"],
        "node_id": row["node_id"],
        "water_depth_m": row["water_depth_m"],
        "rate_of_rise_cm_min": row["rate_of_rise_cm_min"],
        "reported_rate_of_rise_cm_min": row["reported_rate_of_rise_cm_min"],
        "rate_source": row["rate_source"] or "legacy_reported",
        "battery_voltage": row["battery_voltage"],
        "timestamp": row["timestamp"],
        "observed_at": row["observed_at"],
        "received_at": row["received_at"],
        "source": row["source"] or "legacy",
        "timestamp_source": row["timestamp_source"] or "legacy_unknown",
    }
