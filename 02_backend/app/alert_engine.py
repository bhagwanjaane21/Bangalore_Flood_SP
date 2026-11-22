"""Shared prototype alert rules. Pure decisions only; no network or actuator I/O."""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from statistics import median_high, median_low

LEVEL_RANK = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}
RULE_VERSION = "prototype-v1"


@dataclass(frozen=True)
class SensorRule:
    name: str
    lead_time: str
    yellow: float
    orange: float
    red: float


SENSOR_RULES = {
    "SENSOR_01": SensorRule("Rachenahalli Lake Sluice Outlet", "35–45 mins", 0.50, 1.00, 1.50),
    "SENSOR_02": SensorRule("Dasarahalli Mid-Reach Culvert", "15–20 mins", 0.60, 1.10, 1.60),
    "SENSOR_03": SensorRule("Manyata Tech Park Boundary / Gate 4", "0–5 mins (IMMEDIATE)", 0.80, 1.20, 1.50),
}


@dataclass(frozen=True)
class AlertPolicy:
    max_gap_seconds: float = 90
    recovery_samples: int = 2
    recovery_margin_m: float = 0.05
    trend_min_span_seconds: float = 10
    trend_yellow_cm_min: float = 2
    trend_orange_cm_min: float = 5
    window_samples: int = 5


@dataclass(frozen=True)
class Decision:
    level: str
    reason: str
    kind: str
    recovery_count: int = 0
    trend_rate_cm_min: float | None = None
    trend_samples: int = 0
    quality_flags: tuple[str, ...] = ()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def data_freshness(reading: dict, now: datetime, stale_after_seconds: float) -> dict:
    if not reading.get("received_at"):
        return {"data_status": "unknown", "data_age_seconds": None,
                "data_warning": "Timestamp provenance is unknown; current conditions cannot be established."}
    age = max(0.0, (now - parse_time(reading["received_at"])).total_seconds())
    if reading.get("source") != "simulated" and reading.get("observed_at"):
        age = max(age, (now - parse_time(reading["observed_at"])).total_seconds())
    stale = age > stale_after_seconds
    return {"data_status": "stale" if stale else "fresh", "data_age_seconds": round(age, 1),
            "data_warning": "Reading is stale; current conditions cannot be established." if stale else None}


def depth_level(rule: SensorRule, depth: float) -> str:
    for level, threshold in (("RED", rule.red), ("ORANGE", rule.orange), ("YELLOW", rule.yellow)):
        if depth >= threshold:
            return level
    return "GREEN"


def recent_trend(history: list[dict], policy: AlertPolicy) -> tuple[float | None, list[float], int, tuple[str, ...]]:
    """Use a contiguous same-source tail; client-reported rates never qualify."""
    flags = []
    if not history or not history[-1].get("observed_at"):
        return None, [], 0, ("timestamp_unknown",)
    tail = [history[-1]]
    for previous in reversed(history[:-1]):
        current = tail[0]
        if not previous.get("observed_at") or not previous.get("received_at"):
            flags.append("timestamp_unknown")
            break
        if previous.get("source") != current.get("source"):
            flags.append("source_changed")
            break
        observed_gap = (parse_time(current["observed_at"]) - parse_time(previous["observed_at"])).total_seconds()
        receipt_gap = (parse_time(current["received_at"]) - parse_time(previous["received_at"])).total_seconds()
        if not 0 < observed_gap <= policy.max_gap_seconds or not 0 <= receipt_gap <= policy.max_gap_seconds:
            flags.append("sampling_gap")
            break
        tail.insert(0, previous)
        if len(tail) >= policy.window_samples:
            break
    rates = []
    for previous, current in zip(tail, tail[1:]):
        elapsed = (parse_time(current["observed_at"]) - parse_time(previous["observed_at"])).total_seconds()
        rate = (current["water_depth_m"] - previous["water_depth_m"]) / elapsed * 6000
        if not math.isfinite(rate):
            return None, [], len(tail), tuple(flags + ["trend_unavailable"])
        rates.append(round(rate, 6))
    if rates and abs(rates[-1]) >= 30:
        flags.append("abrupt_change")
    if len(tail) < 3:
        return None, [], len(tail), tuple(flags + ["insufficient_trend_samples"])
    span = (parse_time(tail[-1]["observed_at"]) - parse_time(tail[0]["observed_at"])).total_seconds()
    if span < policy.trend_min_span_seconds:
        return None, [], len(tail), tuple(flags + ["trend_span_too_short"])
    # Taking the midpoint this way avoids overflowing when two valid finite
    # rates are both close to the floating-point maximum.
    trend = median_low(rates) / 2 + median_high(rates) / 2
    return round(trend, 6), rates, len(tail), tuple(flags)


def evaluate_reading(reading: dict, history: list[dict], previous_state: dict | None,
                     now: datetime, policy: AlertPolicy) -> Decision:
    rule = SENSOR_RULES[reading["node_id"]]
    old_level = previous_state["level"] if previous_state else "GREEN"
    candidate = depth_level(rule, reading["water_depth_m"])
    kind = "depth" if candidate != "GREEN" else "normal"
    reason = (f"Depth {reading['water_depth_m']:.3f} m gives {candidate} under prototype depth thresholds.")
    freshness = data_freshness(reading, now, policy.max_gap_seconds)
    fresh = freshness["data_status"] == "fresh"
    trend, rates, sample_count, quality_flags = recent_trend(history, policy)
    flags = list(quality_flags)
    if reading.get("rate_source") == "reported":
        flags.append("reported_rate_unverified")
    if not fresh:
        flags.append("data_" + freshness["data_status"])
    if fresh and trend is not None and len(rates) >= 2:
        for level, threshold in (("ORANGE", policy.trend_orange_cm_min),
                                 ("YELLOW", policy.trend_yellow_cm_min)):
            if trend >= threshold and min(rates[-2:]) >= threshold:
                if LEVEL_RANK[level] > LEVEL_RANK[candidate]:
                    candidate = level
                    kind = "trend"
                    reason = (f"Sustained rise: median {trend:.3f} cm/min and both latest intervals "
                              f">= {threshold:g} cm/min across {sample_count} observations.")
                break
    count = 0
    if LEVEL_RANK[candidate] < LEVEL_RANK[old_level]:
        release_depth = round(getattr(rule, old_level.lower()) - policy.recovery_margin_m, 6)
        if not fresh:
            candidate = old_level
            kind = "held"
            reason = f"Holding {old_level}: stale or unknown data cannot confirm recovery."
        elif reading["water_depth_m"] > release_depth:
            candidate = old_level
            kind = "held"
            reason = f"Holding {old_level}: recovery requires depth <= {release_depth:.3f} m."
        else:
            # A sampling/source break discards previous recovery evidence.
            prior_count = previous_state["recovery_count"] if previous_state and sample_count >= 2 else 0
            count = prior_count + 1
            if count < policy.recovery_samples:
                candidate = old_level
                kind = "held"
                reason = (f"Holding {old_level}: recovery observation {count}/{policy.recovery_samples} "
                          f"below {release_depth:.3f} m.")
            else:
                kind = "recovery"
                reason = (f"Recovery {old_level} -> {candidate} confirmed by {count} fresh observations "
                          f"below {release_depth:.3f} m. " + reason)
                count = 0
    if not fresh and kind != "held":
        reason += " Historical/unknown observation; this does not establish current conditions."
    return Decision(candidate, reason, kind, count, trend, sample_count, tuple(dict.fromkeys(flags)))


def alert_snapshot(reading: dict) -> dict:
    rule = SENSOR_RULES.get(reading["node_id"])
    level = reading.get("alert_level")
    actions = {
        "GREEN": "Continue monitoring.",
        "YELLOW": "Notify the designated operator and monitor the trend.",
        "ORANGE": "Prepare the agreed local response and check current conditions.",
        "RED": "Escalate immediately to the designated operator using the agreed local response plan.",
    }
    return {
        "name": rule.name if rule else reading["node_id"],
        "lead_time": rule.lead_time if rule else "Unknown",
        "lead_time_source": "unvalidated_assumption",
        "alert_level": level,
        "alert_reason": reading.get("alert_reason"),
        "alert_kind": reading.get("alert_kind"),
        "alert_action": actions.get(level),
        "alert_rule_version": reading.get("alert_rule_version"),
        "recovery_count": reading.get("alert_recovery_count") or 0,
        "trend_rate_cm_min": reading.get("alert_trend_rate_cm_min"),
        "trend_sample_count": reading.get("alert_trend_samples") or 0,
        "quality_flags": json.loads(reading.get("alert_quality_flags") or "[]"),
    }
