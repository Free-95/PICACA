"""
PICACA-Lite Student 2:
SWaT Stage 1 B1 physics-only command authorization baseline.

This module consumes Yash's B1 handoff schema and produces explainable
ALLOW / DELAY_ALERT / BLOCK_ALARM decisions using only physical state,
causal trend, and sensor consistency. It intentionally does not use
context_risk; that is reserved for B3/B4.
"""

import argparse
import csv
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DELAY_ALERT = "DELAY_ALERT"
    BLOCK_ALARM = "BLOCK_ALARM"


class LevelZone(str, Enum):
    LOW_CRITICAL = "LOW_CRITICAL"
    LOW_WARNING = "LOW_WARNING"
    NORMAL = "NORMAL"
    HIGH_WARNING = "HIGH_WARNING"
    HIGH_CRITICAL = "HIGH_CRITICAL"


class TrendState(str, Enum):
    FALLING_FAST = "FALLING_FAST"
    FALLING = "FALLING"
    STABLE = "STABLE"
    RISING = "RISING"
    RISING_FAST = "RISING_FAST"


class CommandEffect(str, Enum):
    FILLING = "FILLING"
    DRAINING = "DRAINING"
    STOP_FILLING = "STOP_FILLING"
    STOP_DRAINING = "STOP_DRAINING"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class B1Config:
    low_critical: float = 20.0
    low_warning: float = 25.0
    normal_low: float = 30.0
    normal_high: float = 75.0
    high_warning: float = 80.0
    high_critical: float = 90.0
    fixed_lookahead_seconds: float = 5.0
    nominal_fill_rate_pct_s: float = 0.05
    nominal_drain_rate_pct_s: float = 0.05
    stable_trend_abs_pct_s: float = 0.015
    fast_trend_abs_pct_s: float = 0.08
    delay_risk_threshold: float = 0.55
    block_risk_threshold: float = 0.85
    sensor_spike_mm_5s: float = 50.0
    contradictory_trend_pct_s: float = 0.0887
    active_flow_threshold: float = 1.5


@dataclass(frozen=True)
class B1Event:
    timestamp: str
    command_type: str
    command_value: str
    level_percent: float
    trend_pct_s: float
    raw_trend_mm_5s: Optional[float]
    pump_state: int
    valve_state: str
    flow_value: float
    label: str = ""


@dataclass(frozen=True)
class B1Result:
    timestamp: str
    label: str
    command_type: str
    level_percent: float
    trend_pct_s: float
    level_zone: LevelZone
    trend_state: TrendState
    command_effect: CommandEffect
    lookahead_seconds: float
    projected_level: float
    overflow_risk: float
    underflow_risk: float
    physics_risk: float
    sensor_risk: float
    final_risk: float
    decision: Decision
    reason: str


def clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def parse_float(value: object, default: Optional[float] = None) -> Optional[float]:
    text = str(value or "").strip()
    if not text or text.upper() in {"NONE", "NAN", "NULL"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def parse_int(value: object, default: int = 0) -> int:
    number = parse_float(value)
    if number is None:
        return default
    return int(number)


def convert_yash_trend_to_pct_s(recent_level_trend: float) -> float:
    # Yash's trend is LIT101(t) - LIT101(t-5) in millimeters.
    # LIT101 / 1000 * 100 means 10 mm = 1 percent; divide by 5 seconds.
    return recent_level_trend / 50.0


def classify_level(level_percent: float, config: B1Config) -> LevelZone:
    if level_percent <= config.low_critical:
        return LevelZone.LOW_CRITICAL
    if level_percent <= config.low_warning:
        return LevelZone.LOW_WARNING
    if level_percent < config.high_warning:
        return LevelZone.NORMAL
    if level_percent < config.high_critical:
        return LevelZone.HIGH_WARNING
    return LevelZone.HIGH_CRITICAL


def classify_trend(trend_pct_s: float, config: B1Config) -> TrendState:
    if trend_pct_s <= -config.fast_trend_abs_pct_s:
        return TrendState.FALLING_FAST
    if trend_pct_s < -config.stable_trend_abs_pct_s:
        return TrendState.FALLING
    if trend_pct_s >= config.fast_trend_abs_pct_s:
        return TrendState.RISING_FAST
    if trend_pct_s > config.stable_trend_abs_pct_s:
        return TrendState.RISING
    return TrendState.STABLE


def infer_command_effect(event: B1Event) -> CommandEffect:
    command = event.command_type.upper()
    valve = event.valve_state.upper()
    pump_on = event.pump_state == 1

    if command == "NO_COMMAND":
        return CommandEffect.NEUTRAL
    if "VALVE_OPEN" in command:
        return CommandEffect.FILLING
    if "VALVE_CLOSE" in command:
        return CommandEffect.STOP_FILLING
    if "P101_ON" in command or "P102_ON" in command:
        return CommandEffect.DRAINING
    if "P101_OFF" in command or "P102_OFF" in command:
        if valve == "OPEN" or event.flow_value > 0.2:
            return CommandEffect.STOP_DRAINING
        return CommandEffect.NEUTRAL
    if "VALVE_TRANSITION" in command:
        if pump_on:
            return CommandEffect.NEUTRAL
        return CommandEffect.FILLING
    return CommandEffect.NEUTRAL


def projected_trend(event: B1Event, effect: CommandEffect, config: B1Config) -> float:
    trend = event.trend_pct_s
    if effect == CommandEffect.FILLING:
        return max(trend, config.nominal_fill_rate_pct_s)
    if effect == CommandEffect.DRAINING:
        return min(trend, -config.nominal_drain_rate_pct_s)
    if effect == CommandEffect.STOP_DRAINING:
        return max(trend, config.nominal_fill_rate_pct_s / 2.0)
    if effect == CommandEffect.STOP_FILLING:
        return min(trend, 0.0)
    return trend


def overflow_risk(projected_level: float, config: B1Config) -> float:
    if projected_level <= config.normal_high:
        return 0.0
    return clip((projected_level - config.normal_high) / (config.high_critical - config.normal_high))


def underflow_risk(projected_level: float, config: B1Config) -> float:
    if projected_level >= config.normal_low:
        return 0.0
    return clip((config.normal_low - projected_level) / (config.normal_low - config.low_critical))


def sensor_consistency_risk(event: B1Event, config: B1Config) -> tuple[float, list[str]]:
    reasons: list[str] = []
    risk = 0.0
    valve_open = event.valve_state.upper() == "OPEN"
    pump_on = event.pump_state == 1

    if event.raw_trend_mm_5s is not None and abs(event.raw_trend_mm_5s) > config.sensor_spike_mm_5s:
        reasons.append("Large 5-second LIT101 jump suggests possible sensor spoofing.")
        risk = max(risk, 0.90)

    if (valve_open or event.flow_value > config.active_flow_threshold) and not pump_on:
        if event.trend_pct_s < -config.contradictory_trend_pct_s:
            reasons.append("Inflow is active but the tank level is falling.")
            risk = max(risk, 0.75)

    if pump_on and event.valve_state.upper() == "CLOSED":
        if event.trend_pct_s > config.contradictory_trend_pct_s:
            reasons.append("Outflow pump is active but the tank level is rising.")
            risk = max(risk, 0.75)

    return risk, reasons


def decide_b1(event: B1Event, config: B1Config = B1Config()) -> B1Result:
    level_zone = classify_level(event.level_percent, config)
    trend_state = classify_trend(event.trend_pct_s, config)
    effect = infer_command_effect(event)
    future_trend = projected_trend(event, effect, config)
    projected_level = event.level_percent + future_trend * config.fixed_lookahead_seconds
    high_risk = overflow_risk(projected_level, config)
    low_risk = underflow_risk(projected_level, config)
    sensor_risk, reasons = sensor_consistency_risk(event, config)

    if effect in {CommandEffect.FILLING, CommandEffect.STOP_DRAINING}:
        physics_risk = high_risk
    elif effect == CommandEffect.DRAINING:
        physics_risk = low_risk
    elif effect == CommandEffect.STOP_FILLING:
        physics_risk = low_risk if event.level_percent <= config.low_warning else 0.0
    else:
        physics_risk = max(high_risk, low_risk)

    final_risk = max(physics_risk, sensor_risk)

    def protective_decision(reason: str) -> tuple[Decision, str]:
        if sensor_risk >= config.delay_risk_threshold:
            return (
                Decision.DELAY_ALERT,
                f"{reason}; protective command allowed only after alert because sensor consistency is suspicious.",
            )
        return Decision.ALLOW, reason

    if event.command_type.upper() == "NO_COMMAND":
        decision = Decision.ALLOW
        reasons.append("No actuator command to authorize; row is monitored only.")
    elif effect == CommandEffect.STOP_FILLING and event.level_percent >= config.high_warning:
        decision, reason = protective_decision("Valve close reduces overflow risk.")
        reasons.append(reason)
    elif effect == CommandEffect.DRAINING and event.level_percent >= config.high_warning:
        decision, reason = protective_decision("Pump ON transfers water out of a high tank.")
        reasons.append(reason)
    elif effect == CommandEffect.STOP_DRAINING and event.level_percent <= config.low_warning:
        decision, reason = protective_decision("Pump OFF reduces underflow risk.")
        reasons.append(reason)
    elif effect == CommandEffect.FILLING and projected_level >= config.high_critical:
        decision = Decision.BLOCK_ALARM
        reasons.append("Filling action may push tank into high-critical zone.")
    elif effect == CommandEffect.DRAINING and projected_level <= config.low_critical:
        decision = Decision.BLOCK_ALARM
        reasons.append("Draining action may push tank into low-critical zone.")
    elif final_risk >= config.block_risk_threshold:
        decision = Decision.BLOCK_ALARM
        reasons.append("B1 physical risk is high.")
    elif final_risk >= config.delay_risk_threshold:
        decision = Decision.DELAY_ALERT
        reasons.append("B1 physical risk is medium; delay and alert operator.")
    else:
        decision = Decision.ALLOW
        reasons.append("B1 physical state and projected level are acceptable.")

    return B1Result(
        timestamp=event.timestamp,
        label=event.label,
        command_type=event.command_type,
        level_percent=event.level_percent,
        trend_pct_s=event.trend_pct_s,
        level_zone=level_zone,
        trend_state=trend_state,
        command_effect=effect,
        lookahead_seconds=config.fixed_lookahead_seconds,
        projected_level=projected_level,
        overflow_risk=high_risk,
        underflow_risk=low_risk,
        physics_risk=physics_risk,
        sensor_risk=sensor_risk,
        final_risk=final_risk,
        decision=decision,
        reason="; ".join(reasons),
    )


def row_to_event(row: dict[str, str]) -> B1Event:
    raw_trend = parse_float(row.get("recent_level_trend"), 0.0)
    if raw_trend is None:
        raw_trend = 0.0
    return B1Event(
        timestamp=str(row.get("timestamp", "")),
        command_type=str(row.get("command_type", "NO_COMMAND")),
        command_value=str(row.get("command_value", "")),
        level_percent=float(parse_float(row.get("level_percent"), 0.0) or 0.0),
        trend_pct_s=convert_yash_trend_to_pct_s(raw_trend),
        raw_trend_mm_5s=raw_trend,
        pump_state=parse_int(row.get("pump_state"), 0),
        valve_state=str(row.get("valve_state", "UNKNOWN")),
        flow_value=float(parse_float(row.get("flow_value"), 0.0) or 0.0),
        label=str(row.get("label", "")),
    )


def load_config(config_path: Path | None) -> B1Config:
    if config_path is None:
        return B1Config()
    with config_path.open("r", encoding="utf-8") as config_file:
        values = json.load(config_file)
    allowed = set(B1Config.__dataclass_fields__)
    filtered = {key: value for key, value in values.items() if key in allowed}
    return B1Config(**filtered)


def result_to_row(result: B1Result) -> dict[str, object]:
    return {
        "timestamp": result.timestamp,
        "label": result.label,
        "command_type": result.command_type,
        "level_percent": round(result.level_percent, 4),
        "trend_pct_s": round(result.trend_pct_s, 5),
        "level_zone": result.level_zone.value,
        "trend_state": result.trend_state.value,
        "command_effect": result.command_effect.value,
        "lookahead_seconds": result.lookahead_seconds,
        "projected_level": round(result.projected_level, 4),
        "overflow_risk": round(result.overflow_risk, 4),
        "underflow_risk": round(result.underflow_risk, 4),
        "physics_risk": round(result.physics_risk, 4),
        "sensor_risk": round(result.sensor_risk, 4),
        "final_risk": round(result.final_risk, 4),
        "decision": result.decision.value,
        "reason": result.reason,
    }


def run(input_path: Path, output_path: Path, config: B1Config = B1Config()) -> None:
    with input_path.open("r", newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        results = [decide_b1(row_to_event(row), config) for row in reader]

    rows = [result_to_row(result) for result in results]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for result in results:
        counts[result.decision.value] = counts.get(result.decision.value, 0) + 1

    print(f"Wrote {len(results)} rows to {output_path}")
    print("Decision summary:")
    for decision, count in sorted(counts.items()):
        print(f"  {decision}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("processed/swat_stage1_b1_input.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("processed/swat_b1_full_results.csv"),
    )
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    run(args.input, args.output, load_config(args.config))


if __name__ == "__main__":
    main()
