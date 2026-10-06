#!/usr/bin/env python3
"""
PICACA Stage-1 final runtime (B1-prime -> B2-prime -> V3 -> B3-prime -> B4-prime).

This is the Stage-1-only model. It contains its frozen configuration, B2 envelopes,
and V3 calibration internally. It does not read CSV, JSON, or model files.

Run without arguments for the built-in verification and demonstration:
    python PICACA_STAGE1_FINAL.py

Read one JSON snapshot per line and write one JSON result per line:
    python PICACA_STAGE1_FINAL.py --stdin-json

Run as the Modbus TCP safety proxy in front of the ESP32:
    python PICACA_STAGE1_FINAL.py --modbus-proxy 192.168.1.50

Point the HMI/command client to this computer on TCP port 1502. The proxy reads
the supplied coils, discrete inputs, and input registers, evaluates every actuator-coil
write before forwarding it, and forwards only ALLOW decisions. DELAY and BLOCK
return Modbus exception 6 (busy) and 4 (device failure), respectively.
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import struct
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from statistics import median

VERSION = "PICACA-P1-final-2026-10-06"
FROZEN_CONFIG_SHA256 = "8da6ccd79979f5dea4d6c043a0fc4f6de2a22d21e12a3898964b382b222483c5"
CAL = json.loads(r"""{"_note":"Complete frozen V3 calibration for an independent (e.g. ESP32) re-implementation. Weights and levels are the operating config; the *_model dicts are the fitted lookup tables.","weights":{"frequency":0.2,"repetition":0.2,"timing":0.2,"sequence":0.25,"phase":0.15},"levels":[[0.85,"VERY_HIGH"],[0.6,"HIGH"],[0.3,"MEDIUM"],[0.0,"LOW"]],"laplace_k":1,"min_support":3,"calib_frac":0.8,"all_cmds":["MV101_CHANGE","P101_OFF","P101_ON","P102_ON"],"all_phases":["DRAINING","FILLING","HOLDING","TRANSFERRING","TRANSITIONING"],"freq_model":{"f300":{"p75":1.0,"p90":2.0,"p95":2.0,"p99":3.0,"max":4.0},"f60":{"p95":1.0,"p99":2.0,"max":3.0}},"rep_model":{"rep_p90":2.0,"rep_p99":2.0,"rep_max":2.0,"consec_p99":0.0,"consec_max":6.0},"timing_model":{"tslc":{"mu":4.861584703525863,"sigma":2.2273669577183624,"p1":6.32,"p5":8.0,"p95":2579.8,"p99":2624.0},"tssc":{"mu":5.889743659269763,"sigma":2.7151577657396326,"p1":8.0,"p5":8.0,"p95":4362.5,"p99":5034.700000000011},"n_calib_cmd_rows":534},"markov_prev":{"MV101_CHANGE":{"MV101_CHANGE":0.5240793201133145,"P101_OFF":0.2237960339943343,"P101_ON":0.24645892351274787,"P102_ON":0.0056657223796034},"P101_OFF":{"MV101_CHANGE":0.8901098901098901,"P101_OFF":0.02197802197802198,"P101_ON":0.07692307692307693,"P102_ON":0.01098901098901099},"P101_ON":{"MV101_CHANGE":0.8877551020408163,"P101_OFF":0.07142857142857142,"P101_ON":0.01020408163265306,"P102_ON":0.030612244897959183},"P102_ON":{"MV101_CHANGE":0.14285714285714285,"P101_OFF":0.2857142857142857,"P101_ON":0.42857142857142855,"P102_ON":0.14285714285714285}},"markov_prev_phase":{"MV101_CHANGE":{"DRAINING":{"MV101_CHANGE":0.011111111111111112,"P101_OFF":0.011111111111111112,"P101_ON":0.9555555555555556,"P102_ON":0.022222222222222223},"FILLING":{"MV101_CHANGE":0.024096385542168676,"P101_OFF":0.9518072289156626,"P101_ON":0.012048192771084338,"P102_ON":0.012048192771084338},"HOLDING":{"MV101_CHANGE":0.9662921348314607,"P101_OFF":0.011235955056179775,"P101_ON":0.011235955056179775,"P102_ON":0.011235955056179775},"TRANSFERRING":{"MV101_CHANGE":0.967391304347826,"P101_OFF":0.010869565217391304,"P101_ON":0.010869565217391304,"P102_ON":0.010869565217391304},"TRANSITIONING":{"MV101_CHANGE":0.7333333333333333,"P101_OFF":0.06666666666666667,"P101_ON":0.13333333333333333,"P102_ON":0.06666666666666667}},"P101_OFF":{"DRAINING":{"MV101_CHANGE":0.125,"P101_OFF":0.125,"P101_ON":0.625,"P102_ON":0.125},"FILLING":{"MV101_CHANGE":0.2,"P101_OFF":0.4,"P101_ON":0.2,"P102_ON":0.2},"HOLDING":{"MV101_CHANGE":0.25,"P101_OFF":0.25,"P101_ON":0.25,"P102_ON":0.25},"TRANSFERRING":{"MV101_CHANGE":0.16666666666666666,"P101_OFF":0.16666666666666666,"P101_ON":0.5,"P102_ON":0.16666666666666666},"TRANSITIONING":{"MV101_CHANGE":0.9642857142857143,"P101_OFF":0.011904761904761904,"P101_ON":0.011904761904761904,"P102_ON":0.011904761904761904}},"P101_ON":{"DRAINING":{"MV101_CHANGE":0.2857142857142857,"P101_OFF":0.14285714285714285,"P101_ON":0.14285714285714285,"P102_ON":0.42857142857142855},"FILLING":{"MV101_CHANGE":0.125,"P101_OFF":0.625,"P101_ON":0.125,"P102_ON":0.125},"HOLDING":{"MV101_CHANGE":0.16666666666666666,"P101_OFF":0.5,"P101_ON":0.16666666666666666,"P102_ON":0.16666666666666666},"TRANSFERRING":{"MV101_CHANGE":0.25,"P101_OFF":0.25,"P101_ON":0.25,"P102_ON":0.25},"TRANSITIONING":{"MV101_CHANGE":0.9662921348314607,"P101_OFF":0.011235955056179775,"P101_ON":0.011235955056179775,"P102_ON":0.011235955056179775}},"P102_ON":{"DRAINING":{"MV101_CHANGE":0.25,"P101_OFF":0.25,"P101_ON":0.25,"P102_ON":0.25},"FILLING":{"MV101_CHANGE":0.2,"P101_OFF":0.4,"P101_ON":0.2,"P102_ON":0.2},"HOLDING":{"MV101_CHANGE":0.25,"P101_OFF":0.25,"P101_ON":0.25,"P102_ON":0.25},"TRANSFERRING":{"MV101_CHANGE":0.16666666666666666,"P101_OFF":0.16666666666666666,"P101_ON":0.5,"P102_ON":0.16666666666666666},"TRANSITIONING":{"MV101_CHANGE":0.25,"P101_OFF":0.25,"P101_ON":0.25,"P102_ON":0.25}}},"markov_phase":{"DRAINING":{"MV101_CHANGE":0.020618556701030927,"P101_OFF":0.010309278350515464,"P101_ON":0.9278350515463918,"P102_ON":0.041237113402061855},"FILLING":{"MV101_CHANGE":0.022222222222222223,"P101_OFF":0.9555555555555556,"P101_ON":0.011111111111111112,"P102_ON":0.011111111111111112},"HOLDING":{"MV101_CHANGE":0.945054945054945,"P101_OFF":0.03296703296703297,"P101_ON":0.01098901098901099,"P102_ON":0.01098901098901099},"TRANSFERRING":{"MV101_CHANGE":0.9270833333333334,"P101_OFF":0.010416666666666666,"P101_ON":0.052083333333333336,"P102_ON":0.010416666666666666},"TRANSITIONING":{"MV101_CHANGE":0.9777777777777777,"P101_OFF":0.005555555555555556,"P101_ON":0.011111111111111112,"P102_ON":0.005555555555555556}},"markov_support_prev":{"MV101_CHANGE":{"MV101_CHANGE":184,"P102_ON":1,"P101_OFF":78,"P101_ON":86},"P101_OFF":{"MV101_CHANGE":80,"P101_ON":6,"P101_OFF":1},"P101_ON":{"MV101_CHANGE":86,"P101_OFF":6,"P102_ON":2},"P102_ON":{"P101_OFF":1,"P101_ON":2}},"markov_vocab_size":4,"phase_rarity_model":{"DRAINING":{"MV101_CHANGE":0.020618556701030927,"P101_OFF":0.010309278350515464,"P101_ON":0.9278350515463918,"P102_ON":0.041237113402061855},"FILLING":{"MV101_CHANGE":0.022222222222222223,"P101_OFF":0.9555555555555556,"P101_ON":0.011111111111111112,"P102_ON":0.011111111111111112},"HOLDING":{"MV101_CHANGE":0.945054945054945,"P101_OFF":0.03296703296703297,"P101_ON":0.01098901098901099,"P102_ON":0.01098901098901099},"TRANSFERRING":{"MV101_CHANGE":0.9270833333333334,"P101_OFF":0.010416666666666666,"P101_ON":0.052083333333333336,"P102_ON":0.010416666666666666},"TRANSITIONING":{"MV101_CHANGE":0.9777777777777777,"P101_OFF":0.005555555555555556,"P101_ON":0.011111111111111112,"P102_ON":0.005555555555555556}}}""")
ENVELOPE_ROWS = json.loads(r"""[{"state_key":"Closed_0_0","flow_bin":"LOW_FLOW","support_count":34711,"support_status":"PREFERRED","flow_epsilon":0.0,"flow_p50_threshold":2.547161,"deadband":0.07849999999999681,"lower_tail_width":0.027480000000002752,"upper_tail_width":0.10597999999999956,"median_change":0.0,"P1":-0.07458000000000312,"P5":-0.047100000000000364,"P95":0.0588799999999992,"P99":0.16485999999999876,"expected_direction":"STABLE","stable_envelope_tight":false,"blocklisted":false},{"state_key":"Closed_0_0","flow_bin":"MEDIUM_FLOW","support_count":281,"support_status":"THIN","flow_epsilon":0.0,"flow_p50_threshold":2.547161,"deadband":0.07849999999999681,"lower_tail_width":0.043176000000003115,"upper_tail_width":0.06672799999999346,"median_change":0.21982000000000423,"P1":0.0745739999999978,"P5":0.11775000000000091,"P95":0.314020000000005,"P99":0.3807479999999985,"expected_direction":"RISING","stable_envelope_tight":true,"blocklisted":false},{"state_key":"Closed_1_0","flow_bin":"LOW_FLOW","support_count":44073,"support_status":"PREFERRED","flow_epsilon":0.0,"flow_p50_threshold":2.547161,"deadband":0.07849999999999681,"lower_tail_width":0.08636000000000188,"upper_tail_width":0.06672999999999546,"median_change":-0.23944000000000187,"P1":-0.47102999999999606,"P5":-0.3846699999999942,"P95":-0.09420999999999821,"P99":-0.027480000000002745,"expected_direction":"FALLING","stable_envelope_tight":true,"blocklisted":false},{"state_key":"Closed_1_0","flow_bin":"MEDIUM_FLOW","support_count":1,"support_status":"THIN","flow_epsilon":0.0,"flow_p50_threshold":2.547161,"deadband":0.07849999999999681,"lower_tail_width":0.02,"upper_tail_width":0.02,"median_change":-0.2315900000000056,"P1":-0.2315900000000056,"P5":-0.2315900000000056,"P95":-0.2315900000000056,"P99":-0.2315900000000056,"expected_direction":"FALLING","stable_envelope_tight":true,"blocklisted":false},{"state_key":"Open_0_0","flow_bin":"HIGH_FLOW","support_count":19396,"support_status":"PREFERRED","flow_epsilon":0.0,"flow_p50_threshold":2.547161,"deadband":0.07849999999999681,"lower_tail_width":0.05887050000000159,"upper_tail_width":0.09420999999999818,"median_change":0.23552000000000817,"P1":0.015709500000001524,"P5":0.07458000000000312,"P95":0.4160799999999995,"P99":0.5102899999999977,"expected_direction":"RISING","stable_envelope_tight":true,"blocklisted":false},{"state_key":"Open_0_0","flow_bin":"MEDIUM_FLOW","support_count":19160,"support_status":"PREFERRED","flow_epsilon":0.0,"flow_p50_threshold":2.547161,"deadband":0.07849999999999681,"lower_tail_width":0.06281949999999938,"upper_tail_width":0.10597999999999963,"median_change":0.23944000000000187,"P1":0.011770000000001345,"P5":0.07458950000000072,"P95":0.420010000000002,"P99":0.5259900000000016,"expected_direction":"RISING","stable_envelope_tight":true,"blocklisted":false},{"state_key":"Open_1_0","flow_bin":"HIGH_FLOW","support_count":88201,"support_status":"PREFERRED","flow_epsilon":0.0,"flow_p50_threshold":2.547161,"deadband":0.07849999999999681,"lower_tail_width":0.09420999999999255,"upper_tail_width":0.09028999999999313,"median_change":0.0,"P1":-0.2551499999999976,"P5":-0.16094000000000505,"P95":0.16878000000000384,"P99":0.25906999999999697,"expected_direction":"STABLE","stable_envelope_tight":false,"blocklisted":true},{"state_key":"Open_1_0","flow_bin":"MEDIUM_FLOW","support_count":87480,"support_status":"PREFERRED","flow_epsilon":0.0,"flow_p50_threshold":2.547161,"deadband":0.07849999999999681,"lower_tail_width":0.09419999999999507,"upper_tail_width":0.09028000000000133,"median_change":0.007850000000001956,"P1":-0.24728999999999815,"P5":-0.15309000000000308,"P95":0.17664000000000327,"P99":0.2669200000000046,"expected_direction":"STABLE","stable_envelope_tight":false,"blocklisted":true},{"state_key":"Transition_1_0","flow_bin":"MEDIUM_FLOW","support_count":2,"support_status":"THIN","flow_epsilon":0.0,"flow_p50_threshold":2.547161,"deadband":0.07849999999999681,"lower_tail_width":0.02,"upper_tail_width":0.02,"median_change":-0.243365,"P1":-0.32029990000000125,"P5":-0.3140195000000011,"P95":-0.17271049999999888,"P99":-0.16643009999999878,"expected_direction":"FALLING","stable_envelope_tight":true,"blocklisted":false}]""")
ENVELOPES = {(r["state_key"], r["flow_bin"]): r for r in ENVELOPE_ROWS}

# Frozen revised Stage-1 policy.
B1 = {
    "low_critical": 20.0, "low_warning": 25.0, "normal_low": 30.0,
    "normal_high": 75.0, "high_warning": 80.0, "high_critical": 90.0,
    "lookahead_s": 5.0, "nominal_fill_pct_s": 0.05,
    "nominal_drain_pct_s": 0.05, "delay": 0.55, "block": 0.85,
    "sensor_spike_mm_5s": 50.0, "contradictory_pct_s": 0.0887,
    "active_flow": 1.5,
}
B3 = {
    "physics_weight": 0.35, "sensor_weight": 0.20, "b2_weight": 0.25,
    "context_weight": 0.20, "delay": 0.55, "block": 0.85,
    "hard_gate": 0.85, "agreement": 0.50, "b2_lookback_s": 30,
}
B4 = {"min_support": 300, "max_width_ratio": 2.0,
      "max_opposing_pct_s": 0.015, "monitor_risk": 0.85,
      "max_lookahead_s": 60.0}
FLOW_EPSILON = float(ENVELOPE_ROWS[0]["flow_epsilon"])
FLOW_P50 = float(ENVELOPE_ROWS[0]["flow_p50_threshold"])
DEADBAND = float(ENVELOPE_ROWS[0]["deadband"])
BLOCKLIST = {("Open_1_0", "HIGH_FLOW"), ("Open_1_0", "MEDIUM_FLOW")}

ALLOW, DELAY, BLOCK = "ALLOW", "DELAY_ALERT", "BLOCK_ALARM"
SEVERITY = {ALLOW: 0, DELAY: 1, BLOCK: 2}
DECISION_FROM_SEVERITY = {0: ALLOW, 1: DELAY, 2: BLOCK}

# Supplied hardware register map. Protocol offsets are zero-based: printed
# addresses 00001/10001/30001/40001 are sent as offsets 0 in the Modbus PDU.
REGISTER_MAP = {
    "P101_CMD_COIL": 0,          # 00001, FC01/05: 0=OFF, 1=ON
    "MV101_CMD_COIL": 1,         # 00002, FC01/05: 0=CLOSE, 1=OPEN
    "P101_ACTUAL_DI": 0,         # 10001, FC02
    "FLOAT_SWITCH_SAFE_DI": 1,   # 10002, FC02: 0=TRIPPED, 1=SAFE
    "LIT101_INPUT": 0,           # 30001, FC04: 0..1000, 855=85.5%
    "FIT101_INPUT": 1,           # 30002, FC04: scaled by 10
    "FAULT_MODE_HOLDING": 0,     # 40001, FC03/06/16
    "FAULT_VALUE_HOLDING": 1,    # 40002, FC03/06/16
}
COMMAND_CODE = {
    0: "NO_COMMAND", 1: "VALVE_OPEN", 2: "VALVE_CLOSE",
    3: "P101_ON", 4: "P101_OFF", 5: "P102_ON", 6: "P102_OFF",
    7: "VALVE_TRANSITION",
}
VALVE_CODE = {0: "CLOSED", 1: "OPEN", 2: "TRANSITION"}


def clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(v)))


def ramp(value, low, high):
    return 0.0 if high <= low else clip((float(value) - low) / (high - low))


def risk_above(projected):
    return 0.0 if projected <= B1["normal_high"] else clip(
        (projected - B1["normal_high"]) / (B1["high_critical"] - B1["normal_high"]))


def risk_below(projected):
    return 0.0 if projected >= B1["normal_low"] else clip(
        (B1["normal_low"] - projected) / (B1["normal_low"] - B1["low_critical"]))


def max_decision(a, b):
    return DECISION_FROM_SEVERITY[max(SEVERITY[a], SEVERITY[b])]


def normalize_command(value):
    value = str(value or "NO_COMMAND").strip().upper()
    aliases = {"MV101_OPEN": "VALVE_OPEN", "MV101_CLOSE": "VALVE_CLOSE",
               "MV101_CHANGE": "VALVE_TRANSITION", "NONE": "NO_COMMAND"}
    value = aliases.get(value, value)
    allowed = set(COMMAND_CODE.values())
    parts = value.split(";")
    if value not in allowed and not (len(parts) > 1 and all(p in allowed - {"NO_COMMAND", "VALVE_TRANSITION"} for p in parts)):
        raise ValueError("Unsupported proposed_command: " + value)
    return value


def normalize_valve(value):
    if isinstance(value, (int, float)):
        return VALVE_CODE.get(int(value), "UNKNOWN")
    value = str(value).strip().upper()
    return value if value in {"OPEN", "CLOSED", "TRANSITION"} else "UNKNOWN"


@dataclass
class Snapshot:
    timestamp_s: float
    lit101_mm: float
    fit101_m3_h: float
    mv101_state: str
    p101_on: int
    p102_on: int
    proposed_command: str = "NO_COMMAND"

    @classmethod
    def from_dict(cls, d):
        return cls(
            timestamp_s=float(d.get("timestamp_s", time.time())),
            lit101_mm=float(d["lit101_mm"]),
            fit101_m3_h=float(d["fit101_m3_h"]),
            mv101_state=normalize_valve(d["mv101_state"]),
            p101_on=int(bool(d["p101_on"])),
            p102_on=int(bool(d["p102_on"])),
            proposed_command=normalize_command(d.get("proposed_command", "NO_COMMAND")),
        )


class PicacaStage1:
    """Stateful 1 Hz online implementation of the frozen revised Stage-1 policy."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.sample_index = -1
        self.last_timestamp = None
        self.levels = deque(maxlen=32)
        self.smooth_levels = deque(maxlen=16)
        self.b2_history = deque(maxlen=64)
        self.command_history = deque()
        self.last_any_command_t = None
        self.last_same_command_t = {}
        self.last_command_type = None
        self.same_command_run = 0
        self.last_phase = None
        self.boundary_until = -1
        self.last_stable_valve = "UNKNOWN"

    @staticmethod
    def phase(s):
        pump = bool(s.p101_on or s.p102_on)
        if s.mv101_state == "TRANSITION": return "TRANSITIONING"
        if s.mv101_state == "OPEN" and not pump: return "FILLING"
        if s.mv101_state == "CLOSED" and pump: return "DRAINING"
        if s.mv101_state == "OPEN" and pump: return "TRANSFERRING"
        if s.mv101_state == "CLOSED" and not pump: return "HOLDING"
        return "UNKNOWN"

    @staticmethod
    def flow_bin(flow):
        if flow <= FLOW_EPSILON: return "LOW_FLOW"
        if flow <= FLOW_P50: return "MEDIUM_FLOW"
        return "HIGH_FLOW"

    @staticmethod
    def event_type(command):
        if command.startswith("VALVE_"): return "MV101_CHANGE"
        return command if command != "NO_COMMAND" else "NONE"

    def command_effect(self, command, s):
        if command == "VALVE_OPEN": return "FILLING", "EXPLICIT_OPEN"
        if command == "VALVE_CLOSE": return "STOP_FILLING", "EXPLICIT_CLOSE"
        if "P101_ON" in command or "P102_ON" in command: return "DRAINING", "EXPLICIT_PUMP_ON"
        if "P101_OFF" in command or "P102_OFF" in command:
            effect = "STOP_DRAINING" if s.mv101_state == "OPEN" or s.fit101_m3_h > 0.2 else "NEUTRAL"
            return effect, "EXPLICIT_PUMP_OFF"
        if command == "VALVE_TRANSITION":
            if self.last_stable_valve == "OPEN": return "STOP_FILLING", "POSSIBLE_CLOSING"
            if self.last_stable_valve == "CLOSED": return "FILLING", "POSSIBLE_OPENING"
            return "NEUTRAL", "UNRESOLVED_TRANSITION"
        return "NEUTRAL", "NO_COMMAND"

    def b1_score(self, s, trend_mm_5s, history_ok):
        level = s.lit101_mm / 10.0
        trend = trend_mm_5s / 50.0 if history_ok else 0.0
        effect, direction = self.command_effect(s.proposed_command, s)
        rate = trend
        if effect == "FILLING": rate = max(rate, B1["nominal_fill_pct_s"])
        elif effect == "DRAINING": rate = min(rate, -B1["nominal_drain_pct_s"])
        elif effect == "STOP_DRAINING": rate = max(rate, B1["nominal_fill_pct_s"] / 2.0)
        elif effect == "STOP_FILLING" and s.proposed_command != "VALVE_TRANSITION": rate = min(rate, 0.0)
        projected = level + rate * B1["lookahead_s"]
        high, low = risk_above(projected), risk_below(projected)
        sensor = 0.0
        sensor_reasons = []
        if history_ok and abs(trend_mm_5s) > B1["sensor_spike_mm_5s"]:
            sensor = max(sensor, 0.90); sensor_reasons.append("large LIT101 jump")
        pump = bool(s.p101_on or s.p102_on)
        if (s.mv101_state == "OPEN" or s.fit101_m3_h > B1["active_flow"]) and not pump and trend < -B1["contradictory_pct_s"]:
            sensor = max(sensor, 0.75); sensor_reasons.append("inflow active while level falls")
        if pump and s.mv101_state == "CLOSED" and trend > B1["contradictory_pct_s"]:
            sensor = max(sensor, 0.75); sensor_reasons.append("outflow active while level rises")
        if effect in {"FILLING", "STOP_DRAINING"}: physics = high
        elif effect == "DRAINING": physics = low
        elif effect == "STOP_FILLING": physics = low if level <= B1["low_warning"] else 0.0
        else: physics = max(high, low)
        final = max(physics, sensor)
        protective = ((effect in {"STOP_FILLING", "DRAINING"} and level >= B1["high_warning"]) or
                      (effect == "STOP_DRAINING" and level <= B1["low_warning"]))
        if s.proposed_command == "VALVE_TRANSITION" and direction in {"POSSIBLE_CLOSING", "POSSIBLE_OPENING"}:
            action = BLOCK if physics >= B1["block"] else (DELAY if physics >= B1["delay"] else ALLOW)
            sensor_gate = BLOCK if sensor >= B1["block"] else (DELAY if sensor >= B1["delay"] else ALLOW)
            decision = max_decision(action, sensor_gate)
            why = "direction-aware transition: action=%s, sensor=%s" % (action, sensor_gate)
        elif s.proposed_command == "NO_COMMAND": decision, why = ALLOW, "monitoring row"
        elif protective:
            decision = DELAY if sensor >= B1["delay"] else ALLOW
            why = "protective command" + (" with suspicious sensor" if decision == DELAY else "")
        elif effect == "FILLING" and projected >= B1["high_critical"]:
            decision, why = BLOCK, "filling may reach high-critical level"
        elif effect == "DRAINING" and projected <= B1["low_critical"]:
            decision, why = BLOCK, "draining may reach low-critical level"
        elif final >= B1["block"]: decision, why = BLOCK, "high B1 physical/sensor risk"
        elif final >= B1["delay"]: decision, why = DELAY, "medium B1 physical/sensor risk"
        else: decision, why = ALLOW, "physical state and projection acceptable"
        return {"decision": decision, "physics_risk": physics, "sensor_risk": sensor,
                "level_percent": level, "trend_pct_s": trend, "projected_level": projected,
                "command_effect": effect, "direction_hypothesis": direction,
                "overflow_risk": high, "underflow_risk": low,
                "reason": why + (("; " + ", ".join(sensor_reasons)) if sensor_reasons else "")}

    def b2_score(self, s, phase, smooth, robust_change, history_ok, command_row):
        phase_changed = self.last_phase is not None and phase != self.last_phase
        if command_row or phase_changed:
            self.boundary_until = max(self.boundary_until, self.sample_index + 8)
        state_key = "%s_%d_%d" % (s.mv101_state.title(), s.p101_on, s.p102_on)
        fbin = self.flow_bin(s.fit101_m3_h)
        env = ENVELOPES.get((state_key, fbin))
        scored = bool(history_ok and self.sample_index > self.boundary_until and env and int(env["support_count"]) >= 100)
        risk = 0.0
        reason = "NOT_SCORED"
        if scored:
            actual = robust_change
            expected = float(env["median_change"])
            if actual < float(env["P5"]): exceed = (float(env["P5"]) - actual) / float(env["lower_tail_width"])
            elif actual > float(env["P95"]): exceed = (actual - float(env["P95"])) / float(env["upper_tail_width"])
            else: exceed = 0.0
            base = clip(exceed)
            actual_dir = "STABLE" if abs(actual) <= DEADBAND else ("RISING" if actual > 0 else "FALLING")
            expected_dir = str(env["expected_direction"])
            wrong = ((expected_dir == "RISING" and actual_dir == "FALLING") or
                     (expected_dir == "FALLING" and actual_dir == "RISING"))
            risk = 0.60 + 0.40 * base if wrong and base > 0 else base
            stable_limit = max(abs(float(env["P5"])), abs(float(env["P95"])), DEADBAND)
            if expected_dir == "STABLE" and not bool(env["stable_envelope_tight"]) and abs(actual) > stable_limit:
                risk = max(risk, 0.60)
            risk = clip(risk); reason = "SCORED"
            if (state_key, fbin) in BLOCKLIST:
                risk, reason = 0.0, "BLOCKLISTED"
        return {"risk": risk, "scored": scored, "reason": reason, "state_key": state_key,
                "flow_bin": fbin, "envelope": env, "robust_change_5s_percent": robust_change}

    @staticmethod
    def timing_risk(value, params):
        if value is None or value <= 0 or float(params["sigma"]) == 0: return 0.0
        z = (math.log(value) - float(params["mu"])) / float(params["sigma"])
        cdf = 0.5 * (1 + (1 if z >= 0 else -1) *
                     (1 - math.exp(-0.7854*z*z - 0.5228*abs(z) - 0.4241)))
        return ramp(1.0 - cdf, 0.95, 1.0) * 0.7

    def v3_score(self, s, phase, event):
        now = s.timestamp_s
        while self.command_history and now - self.command_history[0][0] > 300:
            self.command_history.popleft()
        events = list(self.command_history)
        if event != "NONE": events.append((now, event, phase))
        f300 = len(events)
        f60 = sum(1 for t, _, _ in events if now - t <= 60)
        counts = Counter(cmd for _, cmd, _ in events)
        repetition = max(counts.values()) if counts else 0
        consecutive = (self.same_command_run + 1 if event == self.last_command_type else 1) if event != "NONE" else 0
        fm = CAL["freq_model"]; rm = CAL["rep_model"]
        r_freq = max(ramp(f300, fm["f300"]["p90"], fm["f300"]["max"]),
                     ramp(f60, fm["f60"]["p95"], fm["f60"]["max"]))
        r_rep = max(ramp(repetition, rm["rep_p90"], rm["rep_max"]),
                    ramp(consecutive, rm["consec_p99"], rm["consec_max"]))
        if event == "NONE":
            r_time = r_seq = r_phase = 0.0
        else:
            tslc = None if self.last_any_command_t is None else now - self.last_any_command_t
            tssc = None if event not in self.last_same_command_t else now - self.last_same_command_t[event]
            tm = CAL["timing_model"]
            r_time = max(self.timing_risk(tslc, tm["tslc"]), self.timing_risk(tssc, tm["tssc"]))
            all_cmds = CAL["all_cmds"]; fallback = 1.0 / (len(all_cmds) + 1.0)
            prev = self.last_command_type
            if prev is None:
                prob = CAL["markov_phase"].get(phase, {}).get(event, fallback); support = None
            else:
                prob = CAL["markov_prev_phase"].get(prev, {}).get(phase, {}).get(
                    event, CAL["markov_prev"].get(prev, {}).get(event, fallback))
                support = int(CAL["markov_support_prev"].get(prev, {}).get(event, 0))
            r_seq = clip(-math.log(max(prob, 1e-9)) / math.log(max(int(CAL["markov_vocab_size"]), 2)))
            if support is not None and support < int(CAL["min_support"]): r_seq = min(r_seq, 0.80)
            pprob = CAL["phase_rarity_model"].get(phase, {}).get(event, fallback)
            r_phase = clip(-math.log(max(pprob, 1e-9)) / math.log(max(len(all_cmds), 2)))
        parts = [round(clip(x), 4) for x in (r_freq, r_rep, r_time, r_seq, r_phase)]
        weights = CAL["weights"]
        weighted = round(clip(weights["frequency"]*parts[0] + weights["repetition"]*parts[1] +
                              weights["timing"]*parts[2] + weights["sequence"]*parts[3] +
                              weights["phase"]*parts[4]), 4)
        return {"risk": weighted, "components": {"frequency": parts[0], "repetition": parts[1],
                "timing": parts[2], "sequence": parts[3], "phase": parts[4]},
                "features": {"frequency_300s": f300, "frequency_60s": f60,
                "repetition_300s": repetition, "consecutive": consecutive}}

    @staticmethod
    def b3_score(b1, b2_effective, context, command_row):
        score = clip(B3["physics_weight"]*b1["physics_risk"] + B3["sensor_weight"]*b1["sensor_risk"] +
                     B3["b2_weight"]*b2_effective + B3["context_weight"]*context)
        if b1["decision"] == BLOCK: decision, reason = BLOCK, "B1 already blocked"
        elif command_row and max(b1["physics_risk"], b1["sensor_risk"]) >= B3["hard_gate"]:
            decision, reason = BLOCK, "high B1 command risk"
        elif not command_row and max(b1["physics_risk"], b1["sensor_risk"]) >= B3["hard_gate"]:
            decision, reason = max_decision(b1["decision"], DELAY), "high B1 monitoring risk"
        elif command_row and score >= B3["block"]: decision, reason = BLOCK, "high fused score"
        elif not command_row and score >= B3["block"]:
            decision, reason = max_decision(b1["decision"], DELAY), "high fused monitoring score"
        elif score >= B3["delay"]: decision, reason = max_decision(b1["decision"], DELAY), "delay threshold"
        elif context >= B3["agreement"] and b2_effective >= B3["agreement"]:
            decision, reason = max_decision(b1["decision"], DELAY), "B2 and context agree"
        else: decision, reason = b1["decision"], "B3 keeps B1"
        return {"score": score, "decision": decision, "reason": reason}

    @staticmethod
    def b4_score(s, b1, b2, b3, context, history_contiguous):
        env = b2["envelope"]
        reliable = False
        if env and history_contiguous and b1["sensor_risk"] < B1["delay"]:
            med, p5, p95 = float(env["median_change"]), float(env["P5"]), float(env["P95"])
            directional = ((env["expected_direction"] == "RISING" and p5 > 0 and med > 0) or
                           (env["expected_direction"] == "FALLING" and p95 < 0 and med < 0))
            tight = (p95 - p5) <= B4["max_width_ratio"] * abs(med)
            observed = (b2["robust_change_5s_percent"] / 5.0 if history_contiguous else b1["trend_pct_s"])
            contradicts = observed * (1 if med >= 0 else -1) < -B4["max_opposing_pct_s"]
            reliable = directional and tight and int(env["support_count"]) >= B4["min_support"] and not contradicts
        rate = (float(env["median_change"])/5.0 if reliable else b1["trend_pct_s"])
        effect = b1["command_effect"]
        if effect == "FILLING": rate = max(rate, B1["nominal_fill_pct_s"])
        elif effect == "DRAINING": rate = min(rate, -B1["nominal_drain_pct_s"])
        elif effect == "STOP_DRAINING": rate = max(rate, B1["nominal_fill_pct_s"]/2.0)
        elif effect == "STOP_FILLING" and s.proposed_command != "VALVE_TRANSITION": rate = min(rate, 0.0)
        trusted = b1["sensor_risk"] < B1["delay"] and history_contiguous
        horizon = B1["lookahead_s"] + context*(B4["max_lookahead_s"]-B1["lookahead_s"]) if trusted else B1["lookahead_s"]
        projected = b1["level_percent"] + rate*horizon
        high, low = risk_above(projected), risk_below(projected)
        if effect in {"FILLING", "STOP_DRAINING"}: risk = high
        elif effect == "DRAINING": risk = low
        elif effect == "STOP_FILLING": risk = low if b1["level_percent"] <= B1["low_warning"] else 0.0
        else: risk = max(high, low)
        protective = ((effect in {"STOP_FILLING", "DRAINING"} and b1["level_percent"] >= B1["high_warning"]) or
                      (effect == "STOP_DRAINING" and b1["level_percent"] <= B1["low_warning"]))
        command_row = s.proposed_command != "NO_COMMAND"
        eligible = trusted and not protective and horizon > B1["lookahead_s"]
        proposed = BLOCK if risk >= B1["block"] else (DELAY if risk >= B1["delay"] else ALLOW)
        if not command_row: proposed = DELAY if risk >= B4["monitor_risk"] else ALLOW
        known = s.proposed_command in set(COMMAND_CODE.values()) - {"NO_COMMAND"}
        if command_row and (not known or effect == "NEUTRAL") and proposed == BLOCK: proposed = DELAY
        if not eligible: proposed = ALLOW
        decision = max_decision(b3["decision"], proposed)
        return {"decision": decision, "physics_risk": risk, "projected_level": projected,
                "lookahead_seconds": horizon, "group_reliable": reliable,
                "raised_b3": SEVERITY[decision] > SEVERITY[b3["decision"]],
                "reason": "adaptive projection raised B3" if SEVERITY[decision] > SEVERITY[b3["decision"]] else "keep B3"}

    def process(self, snapshot):
        s = snapshot if isinstance(snapshot, Snapshot) else Snapshot.from_dict(snapshot)
        if self.last_timestamp is not None and not (0.5 <= s.timestamp_s - self.last_timestamp <= 1.5):
            self.reset()
        self.sample_index += 1
        command_row = s.proposed_command != "NO_COMMAND"
        phase = self.phase(s); event = self.event_type(s.proposed_command)
        if s.mv101_state in {"OPEN", "CLOSED"}: self.last_stable_valve = s.mv101_state
        self.levels.append(s.lit101_mm)
        smooth = median(list(self.levels)[-9:])
        self.smooth_levels.append(smooth)
        history5 = len(self.levels) >= 6 and len(self.smooth_levels) >= 6
        trend_mm_5s = self.levels[-1] - list(self.levels)[-6] if history5 else 0.0
        robust_change = (self.smooth_levels[-1] - list(self.smooth_levels)[-6]) / 10.0 if history5 else 0.0
        history14 = len(self.levels) >= 14
        b1 = self.b1_score(s, trend_mm_5s, history5)
        prior = [r for idx, r, ok in self.b2_history if self.sample_index - idx <= B3["b2_lookback_s"] and ok and r >= B3["agreement"]]
        recent_second = sorted(prior)[-2] if len(prior) >= 2 else 0.0
        b2 = self.b2_score(s, phase, smooth, robust_change, history5, command_row)
        b2_effective = b2["risk"] if b2["scored"] else recent_second
        v3 = self.v3_score(s, phase, event)
        b3 = self.b3_score(b1, b2_effective, v3["risk"], command_row)
        b4 = self.b4_score(s, b1, b2, b3, v3["risk"], history14)
        self.b2_history.append((self.sample_index, b2["risk"], b2["scored"]))
        if event != "NONE":
            self.command_history.append((s.timestamp_s, event, phase))
            self.same_command_run = self.same_command_run + 1 if event == self.last_command_type else 1
            self.last_command_type = event
            self.last_any_command_t = s.timestamp_s
            self.last_same_command_t[event] = s.timestamp_s
        self.last_phase = phase; self.last_timestamp = s.timestamp_s
        result = {
            "version": VERSION, "stage": "P1", "decision": b4["decision"],
            "decision_code": SEVERITY[b4["decision"]], "command": s.proposed_command,
            "level_mm": round(s.lit101_mm, 3), "phase": phase,
            "b1_decision": b1["decision"], "b3_decision": b3["decision"],
            "b4_decision": b4["decision"], "physics_risk": round(b1["physics_risk"], 6),
            "sensor_risk": round(b1["sensor_risk"], 6), "b2_effective_risk": round(b2_effective, 6),
            "context_risk": round(v3["risk"], 6), "b3_score": round(b3["score"], 6),
            "b4_physics_risk": round(b4["physics_risk"], 6),
            "projected_level_percent": round(b4["projected_level"], 4),
            "lookahead_seconds": round(b4["lookahead_seconds"], 3),
            "b4_group_reliable": b4["group_reliable"],
            "direction_hypothesis": b1["direction_hypothesis"],
            "reason": b1["reason"] + "; " + b3["reason"] + "; " + b4["reason"],
        }
        if SEVERITY[result["b3_decision"]] < SEVERITY[result["b1_decision"]] or SEVERITY[result["b4_decision"]] < SEVERITY[result["b3_decision"]]:
            raise AssertionError("PICACA monotonic safety invariant failed")
        return result


class ModbusTcpClient:
    def __init__(self, host, port=502, unit=1, timeout=3.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.unit = unit; self.tx = 0

    def _request(self, pdu):
        self.tx = (self.tx + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHB", self.tx, 0, len(pdu)+1, self.unit) + pdu)
        head = self._recv(7); tx, proto, length, unit = struct.unpack(">HHHB", head)
        if tx != self.tx or proto != 0 or unit != self.unit: raise IOError("Invalid Modbus response header")
        body = self._recv(length-1)
        if body[0] & 0x80: raise IOError("Modbus exception %d" % body[1])
        return body

    def _recv(self, n):
        data = b""
        while len(data) < n:
            part = self.sock.recv(n-len(data))
            if not part: raise IOError("Modbus connection closed")
            data += part
        return data

    def request_pdu(self, pdu):
        return self._request(pdu)

    def read_bits(self, function, start, count):
        if function not in (1, 2): raise ValueError("Bit reads require function 1 or 2")
        body = self._request(struct.pack(">BHH", function, start, count))
        if body[0] != function: raise IOError("Unexpected Modbus bit-read response")
        return [bool(body[2 + i//8] & (1 << (i % 8))) for i in range(count)]

    def read_registers(self, function, start, count):
        if function not in (3, 4): raise ValueError("Register reads require function 3 or 4")
        body = self._request(struct.pack(">BHH", function, start, count))
        if body[0] != function or body[1] != count*2: raise IOError("Unexpected Modbus register-read response")
        return list(struct.unpack(">" + "H"*count, body[2:]))

    def write_coil(self, address, value):
        body = self._request(struct.pack(">BHH", 5, address, 0xFF00 if value else 0x0000))
        if body[0] != 5: raise IOError("Unexpected Modbus coil-write response")

    def close(self): self.sock.close()


def read_hardware_snapshot(client):
    analog = client.read_registers(4, REGISTER_MAP["LIT101_INPUT"], 2)
    digital = client.read_bits(2, REGISTER_MAP["P101_ACTUAL_DI"], 2)
    commands = client.read_bits(1, REGISTER_MAP["P101_CMD_COIL"], 2)
    return {
        "timestamp_s": time.time(),
        # The hardware supplies percent x10. The Stage-1 compatibility convention uses
        # the same 0..1000 numeric range as canonical LIT101 millimetres.
        "lit101_mm": float(analog[0]),
        "fit101_m3_h": float(analog[1]) / 10.0,
        "mv101_state": "OPEN" if commands[1] else "CLOSED",
        "p101_on": int(digital[0]), "p102_on": 0,
        "float_switch_safe": bool(digital[1]),
        "proposed_command": "NO_COMMAND",
    }


def commands_from_coil_write(pdu):
    """Return proposed PICACA commands for FC05/FC15, or None for other PDUs."""
    if not pdu: return None
    function = pdu[0]; changes = []
    if function == 5 and len(pdu) >= 5:
        address, raw = struct.unpack(">HH", pdu[1:5])
        if raw not in (0x0000, 0xFF00): raise ValueError("Invalid FC05 coil value")
        changes = [(address, raw == 0xFF00)]
    elif function == 15 and len(pdu) >= 6:
        start, count, byte_count = struct.unpack(">HHB", pdu[1:6])
        if count < 1 or byte_count != (count + 7)//8 or len(pdu) < 6 + byte_count:
            raise ValueError("Invalid FC15 payload")
        changes = [(start+i, bool(pdu[6+i//8] & (1 << (i % 8)))) for i in range(count)]
    else:
        return None
    commands = []
    for address, value in changes:
        if address == REGISTER_MAP["P101_CMD_COIL"]: commands.append("P101_ON" if value else "P101_OFF")
        elif address == REGISTER_MAP["MV101_CMD_COIL"]: commands.append("VALVE_OPEN" if value else "VALVE_CLOSE")
    return ";".join(commands) if commands else "NO_COMMAND"


class ProxyState:
    def __init__(self):
        self.engine = PicacaStage1(); self.lock = threading.Lock()
        self.snapshot = None; self.snapshot_time = 0.0; self.samples = 0

    def monitor(self, snapshot):
        with self.lock:
            self.snapshot = dict(snapshot); self.snapshot_time = time.time()
            self.samples += 1
            return self.engine.process(snapshot)

    def authorize(self, command, max_age=2.5):
        with self.lock:
            if self.snapshot is None or time.time() - self.snapshot_time > max_age:
                return {"decision": BLOCK, "decision_code": 2, "command": command,
                        "reason": "hardware input snapshot unavailable or stale"}
            if self.samples < 14:
                return {"decision": BLOCK, "decision_code": 2, "command": command,
                        "reason": "PICACA history warm-up incomplete (%d/14 samples)" % self.samples}
            snap = dict(self.snapshot); snap["timestamp_s"] = time.time(); snap["proposed_command"] = command
            if not snap.get("float_switch_safe", False):
                return {"decision": BLOCK, "decision_code": 2, "command": command,
                        "reason": "hardware float switch is tripped"}
            return self.engine.process(snap)


class SharedModbusDevice:
    """One serialized downstream connection for ESP32 servers with one-client limits."""
    def __init__(self, host, port, unit):
        self.host = host; self.port = port; self.unit = unit
        self.lock = threading.Lock(); self.client = None

    def _connect(self):
        if self.client is None:
            self.client = ModbusTcpClient(self.host, self.port, self.unit)

    def _reset(self):
        if self.client:
            try: self.client.close()
            except Exception: pass
        self.client = None

    def snapshot(self):
        with self.lock:
            try:
                self._connect(); return read_hardware_snapshot(self.client)
            except Exception:
                self._reset(); raise

    def request_pdu(self, pdu):
        with self.lock:
            try:
                self._connect(); return self.client.request_pdu(pdu)
            except Exception:
                self._reset(); raise

    def close(self):
        with self.lock: self._reset()


def _monitor_device(state, device, interval, stop):
    while not stop.is_set():
        try:
            while not stop.is_set():
                out = state.monitor(device.snapshot())
                print(json.dumps({"type":"monitor", **out}, separators=(",", ":")), flush=True)
                stop.wait(interval)
        except Exception as exc:
            print("PICACA monitor reconnect: %s" % exc, file=sys.stderr)
            stop.wait(min(2.0, max(interval, 0.1)))


def _recv_exact(sock, count):
    data = b""
    while len(data) < count:
        part = sock.recv(count-len(data))
        if not part: raise EOFError
        data += part
    return data


def _serve_proxy_connection(upstream, state, device):
    try:
        while True:
            header = _recv_exact(upstream, 7)
            tx, proto, length, request_unit = struct.unpack(">HHHB", header)
            if proto != 0 or length < 2: raise IOError("Invalid upstream Modbus header")
            pdu = _recv_exact(upstream, length-1)
            command = commands_from_coil_write(pdu)
            if command and command != "NO_COMMAND":
                out = state.authorize(command)
                print(json.dumps({"type":"command", **out}, separators=(",", ":")), flush=True)
                if out["decision_code"] != 0:
                    exception = 6 if out["decision_code"] == 1 else 4
                    response = bytes([pdu[0] | 0x80, exception])
                    upstream.sendall(struct.pack(">HHHB", tx, 0, len(response)+1, request_unit) + response)
                    continue
            try:
                response = device.request_pdu(pdu)
            except Exception:
                response = bytes([pdu[0] | 0x80, 4])
            upstream.sendall(struct.pack(">HHHB", tx, 0, len(response)+1, request_unit) + response)
    except (EOFError, ConnectionError, OSError):
        pass
    finally:
        upstream.close()


def run_modbus_proxy(device_host, device_port, unit, interval, listen_host, listen_port):
    state = ProxyState(); stop = threading.Event()
    device = SharedModbusDevice(device_host, device_port, unit)
    monitor = threading.Thread(target=_monitor_device,
        args=(state, device, interval, stop), daemon=True)
    monitor.start()
    server = socket.socket(); server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((listen_host, listen_port)); server.listen(8)
    print("PICACA proxy listening on %s:%d -> %s:%d unit=%d" %
          (listen_host, listen_port, device_host, device_port, unit), file=sys.stderr)
    try:
        while True:
            upstream, _ = server.accept()
            threading.Thread(target=_serve_proxy_connection,
                args=(upstream, state, device), daemon=True).start()
    finally:
        stop.set(); device.close(); server.close()


def warm(engine, start_t, level, flow, valve, p101, p102, seconds=20, slope_mm_s=0.0):
    out = None
    for i in range(seconds):
        out = engine.process({"timestamp_s": start_t+i, "lit101_mm": level+slope_mm_s*i,
                              "fit101_m3_h": flow, "mv101_state": valve,
                              "p101_on": p101, "p102_on": p102, "proposed_command": "NO_COMMAND"})
    return out


def self_test(verbose=True):
    cases = []
    e=PicacaStage1(); warm(e,0,899,2.6,"OPEN",0,0); cases.append(("high-level inlet open", e.process({"timestamp_s":20,"lit101_mm":901,"fit101_m3_h":2.6,"mv101_state":"OPEN","p101_on":0,"p102_on":0,"proposed_command":"VALVE_OPEN"})["decision"], BLOCK))
    e=PicacaStage1(); warm(e,0,850,2.5,"OPEN",1,0); cases.append(("protective inlet close", e.process({"timestamp_s":20,"lit101_mm":850,"fit101_m3_h":2.5,"mv101_state":"OPEN","p101_on":1,"p102_on":0,"proposed_command":"VALVE_CLOSE"})["decision"], ALLOW))
    e=PicacaStage1(); warm(e,0,190,0.0,"CLOSED",0,0); cases.append(("low-level pump on", e.process({"timestamp_s":20,"lit101_mm":190,"fit101_m3_h":0,"mv101_state":"CLOSED","p101_on":0,"p102_on":0,"proposed_command":"P101_ON"})["decision"], BLOCK))
    e=PicacaStage1(); warm(e,0,220,2.5,"OPEN",1,0); cases.append(("protective pump off", e.process({"timestamp_s":20,"lit101_mm":220,"fit101_m3_h":2.5,"mv101_state":"OPEN","p101_on":1,"p102_on":0,"proposed_command":"P101_OFF"})["decision"], ALLOW))
    passed = all(actual == expected for _, actual, expected in cases)
    if verbose:
        for name, actual, expected in cases: print(("PASS" if actual == expected else "FAIL") + " | " + name + " | " + actual)
        print("SELF_TEST=" + ("PASS" if passed else "FAIL"))
    if not passed: raise SystemExit(2)
    return True


def demo():
    engine=PicacaStage1(); warm(engine,1000,850,2.5,"OPEN",1,0)
    sample={"timestamp_s":1020,"lit101_mm":850,"fit101_m3_h":2.5,"mv101_state":"OPEN","p101_on":1,"p102_on":0,"proposed_command":"VALVE_CLOSE"}
    print(json.dumps(engine.process(sample), indent=2))


def main():
    ap=argparse.ArgumentParser(description="CSV-free PICACA final Stage-1 runtime")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--stdin-json", action="store_true", help="one raw Stage-1 snapshot per input line")
    ap.add_argument("--modbus-proxy", metavar="DEVICE_HOST",
                    help="intercept HMI coil writes before forwarding to the downstream device")
    ap.add_argument("--port", type=int, default=502, help="downstream ESP32 Modbus TCP port")
    ap.add_argument("--unit", type=int, default=1)
    ap.add_argument("--listen-host", default="0.0.0.0")
    ap.add_argument("--listen-port", type=int, default=1502,
                    help="HMI connects here (1502 avoids administrator privileges)")
    ap.add_argument("--interval", type=float, default=1.0)
    args=ap.parse_args()
    if not any((args.self_test,args.demo,args.stdin_json,args.modbus_proxy)):
        self_test(); demo(); return
    if args.self_test: self_test()
    if args.demo: demo()
    if args.stdin_json:
        engine=PicacaStage1()
        for line in sys.stdin:
            if line.strip(): print(json.dumps(engine.process(json.loads(line)), separators=(",",":")), flush=True)
    if args.modbus_proxy:
        run_modbus_proxy(args.modbus_proxy,args.port,args.unit,args.interval,
                         args.listen_host,args.listen_port)


if __name__ == "__main__": main()
