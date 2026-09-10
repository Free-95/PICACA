"""
Context Risk V3 — Command-Context Anomaly Model
================================================
PICACA-Lite · Student 1 (Yash) · SWaT Stage 1

PURPOSE
-------
Model ONLY command-context anomalies:
    - command frequency
    - command repetition
    - command timing
    - command sequence (Markov)
    - command rarity within process phase

STRICT EXCLUSIONS
-----------------
No physics reasoning.  No tank-level prediction.  No slope/flow analysis.
No B1/B2/B3/B4 logic.  No Allow/Delay/Block decisions.

CALIBRATION
-----------
All model parameters are fitted on the FIRST 80% of Normal rows ONLY.
Last 20% Normal = validation.  Attack rows = evaluation only.
Labels are NEVER used inside the risk formula.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend safe for any environment
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# PATH CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

INPUT_PATH   = PROJECT_ROOT / "processed" / "swat_stage1_context_features.csv"
OUT_CSV      = PROJECT_ROOT / "processed" / "swat_stage1_context_risk_v3.csv"
OUT_METRICS  = PROJECT_ROOT / "processed" / "context_risk_v3_metrics.csv"
OUT_CMD_MET  = PROJECT_ROOT / "processed" / "context_risk_v3_command_metrics.csv"
OUT_THRESH   = PROJECT_ROOT / "processed" / "context_risk_v3_threshold_curve.csv"
OUT_TOPEX    = PROJECT_ROOT / "processed" / "context_risk_v3_top_examples.csv"
PLOTS_DIR    = PROJECT_ROOT / "plots"    / "context_risk_v3"
ARTIFACTS    = SCRIPT_DIR   / "v3_artifacts"
REPORT_PATH  = PROJECT_ROOT / "CONTEXT_RISK_V3_REPORT.md"

for _d in [PLOTS_DIR, ARTIFACTS]:
    _d.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────
CALIB_FRAC   = 0.80          # first 80% of Normal rows → calibration
LAPLACE_K    = 1             # Laplace smoothing addend
MIN_SUPPORT  = 3             # transitions seen < MIN_SUPPORT → "sparse"
HIGH_SEQ_RISK_THRESH = 0.70  # threshold for "high sequence risk" in sparse audit

WEIGHT_FREQ  = 0.20
WEIGHT_REP   = 0.20
WEIGHT_TIM   = 0.20
WEIGHT_SEQ   = 0.25
WEIGHT_PHASE = 0.15

LEVELS = [(0.85, "VERY_HIGH"), (0.60, "HIGH"), (0.30, "MEDIUM"), (0.00, "LOW")]


# ──────────────────────────────────────────────────────────────────────────────
# 1. LOAD DATA
# ──────────────────────────────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    if not INPUT_PATH.exists():
        sys.exit(f"[ERROR] Input file not found: {INPUT_PATH}")

    print(f"Loading data from {INPUT_PATH} ...")
    df = pd.read_csv(INPUT_PATH, parse_dates=["Timestamp"])
    df = df.sort_values("Timestamp").reset_index(drop=True)

    print(f"  Rows loaded : {len(df):,}")
    print(f"  Normal      : {(df['Normal/Attack']=='Normal').sum():,}")
    print(f"  Attack      : {(df['Normal/Attack']=='Attack').sum():,}")
    print(f"  Columns     : {list(df.columns)}")
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 2. CALIBRATION SPLIT  (NO SHUFFLE — chronological)
# ──────────────────────────────────────────────────────────────────────────────
def create_calibration_split(df: pd.DataFrame):
    normal_idx = df.index[df["Normal/Attack"] == "Normal"].tolist()
    attack_idx = df.index[df["Normal/Attack"] == "Attack"].tolist()

    cut = int(len(normal_idx) * CALIB_FRAC)
    calib_idx = normal_idx[:cut]
    valid_idx = normal_idx[cut:]

    print(f"\nCalibration split (chronological):")
    print(f"  Calibration : {len(calib_idx):,} Normal rows")
    print(f"  Validation  : {len(valid_idx):,} Normal rows")
    print(f"  Attack test : {len(attack_idx):,} rows")
    return calib_idx, valid_idx, attack_idx


# ──────────────────────────────────────────────────────────────────────────────
# 3. COMMAND-ROW FEATURES  (timing — causal only)
# ──────────────────────────────────────────────────────────────────────────────
def create_command_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute time_since_last_command and time_since_same_command causally.
    A row is a command-row iff event_type != 'NONE'.
    Compound events (P101_OFF;P102_ON) count as ONE command-row.
    """
    df = df.copy()
    ts  = df["Timestamp"].values
    et  = df["event_type"].values

    tslc  = np.full(len(df), np.nan, dtype=float)  # time_since_last_command
    tssc  = np.full(len(df), np.nan, dtype=float)  # time_since_same_command

    last_any_ts: float | None = None
    last_cmd_ts: dict[str, float] = {}

    for i in range(len(df)):
        cmd = et[i]
        t   = ts[i].astype("datetime64[s]").astype(float)  # epoch seconds

        if cmd != "NONE":
            # time since last ANY command
            if last_any_ts is not None:
                tslc[i] = t - last_any_ts
            last_any_ts = t

            # time since last occurrence of THIS command type
            if cmd in last_cmd_ts:
                tssc[i] = t - last_cmd_ts[cmd]
            last_cmd_ts[cmd] = t

    df["time_since_last_command"] = tslc
    df["time_since_same_command"] = tssc
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 4. FIT MODELS ON CALIBRATION DATA
# ──────────────────────────────────────────────────────────────────────────────

def fit_frequency_model(df: pd.DataFrame, calib_idx: list) -> dict:
    """
    Fit empirical percentile thresholds for command_frequency and
    command_frequency_60s from Normal calibration rows.
    """
    c = df.loc[calib_idx]
    f300 = c["command_frequency"].values
    f60  = c["command_frequency_60s"].values

    model = {
        "f300": {
            "p75":  float(np.percentile(f300, 75)),
            "p90":  float(np.percentile(f300, 90)),
            "p95":  float(np.percentile(f300, 95)),
            "p99":  float(np.percentile(f300, 99)),
            "max":  float(np.max(f300)),
        },
        "f60": {
            "p95":  float(np.percentile(f60, 95)),
            "p99":  float(np.percentile(f60, 99)),
            "max":  float(np.max(f60)),
        }
    }
    print(f"\nFrequency model (calibrated):")
    print(f"  f300  P75={model['f300']['p75']}  P90={model['f300']['p90']}  "
          f"P95={model['f300']['p95']}  P99={model['f300']['p99']}  Max={model['f300']['max']}")
    print(f"  f60   P95={model['f60']['p95']}   P99={model['f60']['p99']}   Max={model['f60']['max']}")
    return model


def fit_repetition_model(df: pd.DataFrame, calib_idx: list) -> dict:
    c = df.loc[calib_idx]
    rep   = c["command_repetition"].values
    consec = c["consecutive_same_command"].values
    model = {
        "rep_p90":     float(np.percentile(rep, 90)),
        "rep_p99":     float(np.percentile(rep, 99)),
        "rep_max":     float(np.max(rep)),
        "consec_p99":  float(np.percentile(consec, 99)),
        "consec_max":  float(np.max(consec)),
    }
    print(f"\nRepetition model: rep_p90={model['rep_p90']}  rep_p99={model['rep_p99']}  "
          f"consec_p99={model['consec_p99']}  consec_max={model['consec_max']}")
    return model


def fit_timing_model(df: pd.DataFrame, calib_idx: list) -> dict:
    """
    Fit lognormal parameters for inter-command timing on Normal calibration
    command rows.  Only use rows where timing is not NaN (i.e. not first cmd).
    """
    c = df.loc[calib_idx]
    cmd_rows = c[c["event_type"] != "NONE"]

    # time_since_last_command (inter-arrival)
    tslc_vals = cmd_rows["time_since_last_command"].dropna().values
    tslc_vals = tslc_vals[tslc_vals > 0]

    # time_since_same_command
    tssc_vals = cmd_rows["time_since_same_command"].dropna().values
    tssc_vals = tssc_vals[tssc_vals > 0]

    def lognorm_params(vals):
        if len(vals) < 2:
            return {"mu": 0.0, "sigma": 1.0, "p1": 0.0, "p99": 99999.0}
        log_v = np.log(vals)
        return {
            "mu":    float(np.mean(log_v)),
            "sigma": float(np.std(log_v)),
            "p1":    float(np.percentile(vals, 1)),
            "p5":    float(np.percentile(vals, 5)),
            "p95":   float(np.percentile(vals, 95)),
            "p99":   float(np.percentile(vals, 99)),
        }

    model = {
        "tslc": lognorm_params(tslc_vals),
        "tssc": lognorm_params(tssc_vals),
        "n_calib_cmd_rows": int(len(cmd_rows)),
    }
    print(f"\nTiming model (lognormal, {model['n_calib_cmd_rows']} calib cmd rows):")
    print(f"  tslc  mu={model['tslc']['mu']:.2f}  sigma={model['tslc']['sigma']:.2f}  "
          f"p5={model['tslc']['p5']:.0f}s  p95={model['tslc']['p95']:.0f}s")
    return model


def fit_markov_model(df: pd.DataFrame, calib_idx: list) -> dict:
    """
    Learn P(cmd | prev_cmd), P(cmd | prev_cmd, phase), P(cmd | phase)
    from Normal calibration COMMAND ROWS only.
    Uses Laplace smoothing and records support counts.
    """
    c = df.loc[calib_idx]
    cmd_rows = c[c["event_type"] != "NONE"][["event_type", "process_phase"]].copy()
    cmd_rows = cmd_rows.reset_index(drop=True)

    # All known command types and phases
    all_cmds   = sorted(cmd_rows["event_type"].unique().tolist())
    all_phases = sorted(cmd_rows["process_phase"].unique().tolist())

    # ── P(cmd | prev_cmd) ────────────────────────────────────────────────────
    # count[prev][curr] = count
    count_prev = defaultdict(lambda: defaultdict(int))
    for i in range(1, len(cmd_rows)):
        prev = cmd_rows.at[i-1, "event_type"]
        curr = cmd_rows.at[i,   "event_type"]
        count_prev[prev][curr] += 1

    # ── P(cmd | prev_cmd, phase) ─────────────────────────────────────────────
    count_prev_phase = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for i in range(1, len(cmd_rows)):
        prev  = cmd_rows.at[i-1, "event_type"]
        curr  = cmd_rows.at[i,   "event_type"]
        phase = cmd_rows.at[i,   "process_phase"]
        count_prev_phase[prev][phase][curr] += 1

    # ── P(cmd | phase) ────────────────────────────────────────────────────────
    count_phase = defaultdict(lambda: defaultdict(int))
    for _, row in cmd_rows.iterrows():
        count_phase[row["process_phase"]][row["event_type"]] += 1

    # Convert to smoothed probabilities
    vocab_size = len(all_cmds)

    def smooth_probs(counts: dict, vocab: list, k: int = LAPLACE_K) -> dict:
        total = sum(counts.values()) + k * len(vocab)
        return {v: (counts.get(v, 0) + k) / total for v in vocab}

    markov_prev      = {}
    for prev in all_cmds:
        markov_prev[prev] = smooth_probs(count_prev[prev], all_cmds)

    markov_prev_phase = {}
    for prev in all_cmds:
        markov_prev_phase[prev] = {}
        for phase in all_phases:
            markov_prev_phase[prev][phase] = smooth_probs(
                count_prev_phase[prev][phase], all_cmds)

    markov_phase = {}
    for phase in all_phases:
        markov_phase[phase] = smooth_probs(count_phase[phase], all_cmds)

    # Support counts (raw, unsmoothed)
    support_prev = {p: dict(count_prev[p]) for p in all_cmds}

    model = {
        "all_cmds":        all_cmds,
        "all_phases":      all_phases,
        "markov_prev":     markov_prev,
        "markov_prev_phase": markov_prev_phase,
        "markov_phase":    markov_phase,
        "support_prev":    support_prev,
        "count_prev_raw":  {p: dict(count_prev[p]) for p in count_prev},
        "vocab_size":      vocab_size,
        "n_cmd_rows":      len(cmd_rows),
    }

    print(f"\nMarkov model: {len(all_cmds)} command types, "
          f"{len(all_phases)} phases, {len(cmd_rows)} calib command rows")
    return model


def fit_phase_rarity_model(df: pd.DataFrame, calib_idx: list) -> dict:
    """
    Learn P(cmd | phase) from Normal calibration command rows.
    Used separately from the Markov model for phase_rarity risk component.
    """
    c = df.loc[calib_idx]
    cmd_rows = c[c["event_type"] != "NONE"]

    all_phases = sorted(cmd_rows["process_phase"].unique().tolist())
    all_cmds   = sorted(cmd_rows["event_type"].unique().tolist())

    count = defaultdict(lambda: defaultdict(int))
    for _, row in cmd_rows.iterrows():
        count[row["process_phase"]][row["event_type"]] += 1

    model = {}
    for phase in all_phases:
        total = sum(count[phase].values()) + LAPLACE_K * len(all_cmds)
        model[phase] = {
            cmd: (count[phase].get(cmd, 0) + LAPLACE_K) / total
            for cmd in all_cmds
        }

    print(f"\nPhase-rarity model: {len(all_phases)} phases, {len(all_cmds)} commands")
    return model, all_cmds, all_phases


# ──────────────────────────────────────────────────────────────────────────────
# 5. CALCULATE RISK COMPONENTS (row-wise, vectorised where possible)
# ──────────────────────────────────────────────────────────────────────────────

def _sigmoid_risk(value: float, low: float, high: float) -> float:
    """Map value into [0,1] using a linear ramp between low and high thresholds."""
    if high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def calc_risk_cmd_frequency(row, freq_model: dict) -> float:
    f300 = float(row["command_frequency"])
    f60  = float(row["command_frequency_60s"])
    fm   = freq_model

    # Ramp from P90 (start of unusual) to Max (extreme)
    r300 = _sigmoid_risk(f300, fm["f300"]["p90"], fm["f300"]["max"])
    r60  = _sigmoid_risk(f60,  fm["f60"]["p95"],  fm["f60"]["max"])
    return float(np.clip(max(r300, r60), 0.0, 1.0))


def calc_risk_cmd_repetition(row, rep_model: dict) -> float:
    rep    = float(row["command_repetition"])
    consec = float(row["consecutive_same_command"])
    rm     = rep_model

    r_rep   = _sigmoid_risk(rep,    rm["rep_p90"],    rm["rep_max"])
    r_consec = _sigmoid_risk(consec, rm["consec_p99"], rm["consec_max"])
    return float(np.clip(max(r_rep, r_consec), 0.0, 1.0))


def calc_risk_cmd_timing(row, timing_model: dict) -> float:
    """
    Risk based on how unusual the inter-command timing is.
    Only applies on command rows where timing is available.
    """
    if row["event_type"] == "NONE":
        return 0.0

    tslc = row["time_since_last_command"]
    tssc = row["time_since_same_command"]

    tm = timing_model
    risk = 0.0

    def lognorm_tail_risk(t, params) -> float:
        """P(T < t) under lognormal — very short inter-arrival = suspicious."""
        if np.isnan(t) or t <= 0:
            return 0.0
        log_t = np.log(t)
        mu    = params["mu"]
        sigma = params["sigma"]
        if sigma == 0:
            return 0.0
        z = (log_t - mu) / sigma
        # cdf approximation: pnorm(z)
        # Very low z (very fast arrival) → high risk
        # Very high z (very slow arrival) → slight risk
        cdf = 0.5 * (1 + np.sign(z) * (1 - np.exp(-0.7854 * z**2 - 0.5228 * abs(z) - 0.4241)))
        # Fast arrival risk (below p5) → ramp from 0→0.7 as cdf goes 0→0.05
        r_fast = _sigmoid_risk(1.0 - cdf, 0.95, 1.0) * 0.7
        return float(np.clip(r_fast, 0.0, 1.0))

    if not np.isnan(tslc):
        risk = max(risk, lognorm_tail_risk(tslc, tm["tslc"]))
    if not np.isnan(tssc):
        risk = max(risk, lognorm_tail_risk(tssc, tm["tssc"]))

    return float(np.clip(risk, 0.0, 1.0))


def calc_risk_command_sequence(
    row,
    markov_model: dict,
    prev_cmd_state: dict,   # mutable state threaded through iteration
) -> tuple[float, dict]:
    """
    Returns (risk, metadata_dict).
    Uses P(curr | prev) and P(curr | prev, phase).
    Fallback to P(curr | phase) when no previous command is seen.
    """
    cmd   = row["event_type"]
    phase = row["process_phase"]
    mm    = markov_model

    # Only evaluate on command rows
    if cmd == "NONE":
        meta = {
            "prev_cmd": None,
            "curr_cmd": None,
            "curr_phase": phase,
            "transition_support": None,
            "seq_prob": None,
            "is_sparse": False,
        }
        return 0.0, meta

    prev = prev_cmd_state.get("last_cmd")
    all_cmds   = mm["all_cmds"]
    all_phases = mm["all_phases"]

    # ── compute probability ────────────────────────────────────────────────────
    if prev is None:
        # No previous command seen — use P(cmd | phase) as prior
        if phase in mm["markov_phase"]:
            prob = mm["markov_phase"][phase].get(cmd, LAPLACE_K / (len(all_cmds) * LAPLACE_K + 1))
        else:
            prob = LAPLACE_K / (len(all_cmds) * LAPLACE_K + 1)
        support = None
        used_joint = False
    else:
        # Try joint: P(curr | prev, phase)
        if (prev in mm["markov_prev_phase"]
                and phase in mm["markov_prev_phase"][prev]
                and cmd in mm["markov_prev_phase"][prev][phase]):
            prob = mm["markov_prev_phase"][prev][phase][cmd]
        elif prev in mm["markov_prev"]:
            prob = mm["markov_prev"][prev].get(cmd, LAPLACE_K / (len(all_cmds) * LAPLACE_K + 1))
        else:
            prob = LAPLACE_K / (len(all_cmds) * LAPLACE_K + 1)
        used_joint = True

        # Raw support count
        support_raw = mm["support_prev"].get(prev, {}).get(cmd, 0)
        support = int(support_raw)

    is_sparse = (support is not None) and (support < MIN_SUPPORT)

    # ── convert probability to risk ──────────────────────────────────────────
    # Lower probability → higher risk.
    # We use -log(p) normalised to [0,1] via the min/max over all
    # smoothed probabilities in this vocabulary.
    # Min smoothed prob is LAPLACE_K / (N*LAPLACE_K + total), near 0 for large tables.
    # We clip so that extreme values don't dominate.
    eps      = 1e-9
    neg_log  = -np.log(max(prob, eps))
    # Expected range: 0 (prob=1) to ~log(vocab) for uniform distribution
    # High risk should reflect a truly rare transition, not just
    # Laplace-smoothed unseen ones.  Cap at log(vocab_size) to avoid extreme values.
    max_neg_log = np.log(max(mm["vocab_size"], 2))
    risk = float(np.clip(neg_log / max_neg_log, 0.0, 1.0))

    # Apply smoothing cap: if sparse but plausible, cap at 0.80
    if is_sparse:
        risk = min(risk, 0.80)

    # Update mutable state
    prev_cmd_state["last_cmd"] = cmd

    meta = {
        "prev_cmd": prev,
        "curr_cmd": cmd,
        "curr_phase": phase,
        "transition_support": support,
        "seq_prob": float(prob),
        "is_sparse": is_sparse,
    }
    return float(np.clip(risk, 0.0, 1.0)), meta


def calc_risk_phase_rarity(row, phase_model: dict, all_cmds: list, all_phases: list) -> float:
    """
    Risk = how unusual is this command within this phase?
    Strictly command-context: no level, no slope, no physics.
    """
    cmd   = row["event_type"]
    phase = row["process_phase"]

    if cmd == "NONE":
        return 0.0

    if phase in phase_model and cmd in phase_model[phase]:
        prob = phase_model[phase][cmd]
    elif phase in phase_model:
        # Unseen cmd in known phase — assign Laplace fallback
        prob = LAPLACE_K / (sum(phase_model[phase].values()) * len(all_cmds) + LAPLACE_K)
    else:
        prob = LAPLACE_K / (len(all_cmds) * LAPLACE_K + 1)

    eps     = 1e-9
    neg_log = -np.log(max(prob, eps))
    max_nl  = np.log(max(len(all_cmds), 2))
    return float(np.clip(neg_log / max_nl, 0.0, 1.0))


# ──────────────────────────────────────────────────────────────────────────────
# 6. APPLY ALL RISK COMPONENTS IN ONE PASS
# ──────────────────────────────────────────────────────────────────────────────

def calculate_risk_components(
    df: pd.DataFrame,
    freq_model: dict,
    rep_model: dict,
    timing_model: dict,
    markov_model: dict,
    phase_model: dict,
    all_cmds: list,
    all_phases: list,
) -> pd.DataFrame:
    df = df.copy()

    print("\nCalculating risk components ...")
    n = len(df)

    r_freq  = np.zeros(n, dtype=float)
    r_rep   = np.zeros(n, dtype=float)
    r_tim   = np.zeros(n, dtype=float)
    r_seq   = np.zeros(n, dtype=float)
    r_phase = np.zeros(n, dtype=float)

    prev_cmd_state: dict = {"last_cmd": None}

    for i, row in enumerate(df.itertuples(index=False)):
        row_dict = row._asdict()  # named-tuple → dict

        r_freq[i]  = calc_risk_cmd_frequency(row_dict, freq_model)
        r_rep[i]   = calc_risk_cmd_repetition(row_dict, rep_model)
        r_tim[i]   = calc_risk_cmd_timing(row_dict, timing_model)
        r_seq[i], _ = calc_risk_command_sequence(row_dict, markov_model, prev_cmd_state)
        r_phase[i] = calc_risk_phase_rarity(row_dict, phase_model, all_cmds, all_phases)

        if (i + 1) % 50_000 == 0:
            print(f"  ... {i+1:,} / {n:,} rows processed")

    df["risk_cmd_frequency"]   = np.clip(r_freq, 0.0, 1.0).round(4)
    df["risk_cmd_repetition"]  = np.clip(r_rep,  0.0, 1.0).round(4)
    df["risk_cmd_timing"]      = np.clip(r_tim,  0.0, 1.0).round(4)
    df["risk_command_sequence"] = np.clip(r_seq, 0.0, 1.0).round(4)
    df["risk_phase_rarity"]    = np.clip(r_phase, 0.0, 1.0).round(4)

    return df


# ──────────────────────────────────────────────────────────────────────────────
# 7. CANDIDATE SCORES & CONTEXT LEVEL
# ──────────────────────────────────────────────────────────────────────────────

def calculate_candidate_scores(df: pd.DataFrame) -> pd.DataFrame:
    rf  = df["risk_cmd_frequency"]
    rr  = df["risk_cmd_repetition"]
    rt  = df["risk_cmd_timing"]
    rs  = df["risk_command_sequence"]
    rp  = df["risk_phase_rarity"]

    df["context_risk_max"] = np.clip(
        np.maximum.reduce([rf, rr, rt, rs, rp]), 0.0, 1.0
    ).round(4)

    df["context_risk_weighted"] = np.clip(
        WEIGHT_FREQ  * rf
        + WEIGHT_REP   * rr
        + WEIGHT_TIM   * rt
        + WEIGHT_SEQ   * rs
        + WEIGHT_PHASE * rp,
        0.0, 1.0
    ).round(4)

    def level(v):
        for thresh, label in LEVELS:
            if v >= thresh:
                return label
        return "LOW"

    df["context_level_max"]      = df["context_risk_max"].apply(level)
    df["context_level_weighted"] = df["context_risk_weighted"].apply(level)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 8. CONTEXT REASON
# ──────────────────────────────────────────────────────────────────────────────

def generate_context_reason(df: pd.DataFrame, threshold: float = 0.3) -> pd.DataFrame:
    reasons = []
    for _, row in df.iterrows():
        parts = []
        if row["risk_cmd_frequency"]    >= threshold: parts.append("HIGH_COMMAND_FREQUENCY")
        if row["risk_cmd_repetition"]   >= threshold: parts.append("HIGH_COMMAND_REPETITION")
        if row["risk_cmd_timing"]       >= threshold: parts.append("RARE_COMMAND_TIMING")
        if row["risk_command_sequence"] >= threshold: parts.append("RARE_COMMAND_SEQUENCE")
        if row["risk_phase_rarity"]     >= threshold: parts.append("RARE_COMMAND_PHASE")

        if len(parts) == 0:
            reasons.append("NORMAL_CONTEXT")
        elif len(parts) == 1:
            reasons.append(parts[0])
        else:
            reasons.append("MULTIPLE_CONTEXT_ANOMALIES")
    df["context_reason"] = reasons
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 9. SPARSE / UNSEEN TRANSITION AUDIT
# ──────────────────────────────────────────────────────────────────────────────

def sparse_transition_audit(df: pd.DataFrame, markov_model: dict) -> dict:
    """
    Explicitly audit how many Normal command rows involve sparse/unseen transitions
    and how many of those receive high sequence risk.
    """
    cmd_rows  = df[(df["event_type"] != "NONE") & (df["Normal/Attack"] == "Normal")].copy()
    all_cmds  = markov_model["all_cmds"]
    support   = markov_model["support_prev"]

    prev_cmd_state = {"last_cmd": None}
    sparse_count      = 0
    high_risk_sparse  = 0
    transition_counter: dict[str, int] = defaultdict(int)

    for _, row in cmd_rows.iterrows():
        cmd = row["event_type"]
        prev = prev_cmd_state.get("last_cmd")

        if prev is not None:
            sup = support.get(prev, {}).get(cmd, 0)
            if sup < MIN_SUPPORT:
                sparse_count += 1
                transition_counter[f"{prev} → {cmd}"] += 1
                if row["risk_command_sequence"] >= HIGH_SEQ_RISK_THRESH:
                    high_risk_sparse += 1
        prev_cmd_state["last_cmd"] = cmd

    total_cmd = len(cmd_rows)
    top_sparse = sorted(transition_counter.items(), key=lambda x: -x[1])[:10]

    result = {
        "total_normal_cmd_rows":    total_cmd,
        "sparse_transition_rows":   sparse_count,
        "sparse_pct":               round(100 * sparse_count / max(total_cmd, 1), 2),
        "high_risk_sparse_rows":    high_risk_sparse,
        "high_risk_sparse_pct":     round(100 * high_risk_sparse / max(sparse_count, 1), 2),
        "high_seq_risk_threshold":  HIGH_SEQ_RISK_THRESH,
        "top_sparse_transitions":   top_sparse,
    }

    print(f"\nSparse/Unseen Transition Audit (Normal cmd rows):")
    print(f"  Total Normal cmd rows   : {total_cmd:,}")
    print(f"  Sparse (<{MIN_SUPPORT} support) : {sparse_count:,}  ({result['sparse_pct']}%)")
    print(f"  High-risk sparse        : {high_risk_sparse:,}  ({result['high_risk_sparse_pct']}% of sparse)")
    print(f"  Top sparse transitions  : {top_sparse[:5]}")
    return result


# ──────────────────────────────────────────────────────────────────────────────
# 10. EVALUATION
# ──────────────────────────────────────────────────────────────────────────────

def _risk_stats(vals: pd.Series, label: str):
    v = vals.dropna()
    print(f"\n  [{label}] n={len(v):,}")
    print(f"    Mean={v.mean():.4f}  Median={v.median():.4f}  Std={v.std():.4f}")
    print(f"    Min={v.min():.4f}  Max={v.max():.4f}")
    print(f"    P90={np.percentile(v,90):.4f}  P95={np.percentile(v,95):.4f}  P99={np.percentile(v,99):.4f}")
    for t in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        print(f"    >= {t:.1f}: {(v>=t).mean()*100:.2f}%")


def evaluate_model(df: pd.DataFrame, calib_idx: list, valid_idx: list, attack_idx: list):
    norm_val = df.loc[valid_idx]
    atk      = df.loc[attack_idx]

    print("\n" + "="*60)
    print("EVALUATION RESULTS")
    print("="*60)

    for score_col in ["context_risk_max", "context_risk_weighted"]:
        print(f"\n{'─'*50}")
        print(f"  Score: {score_col}")
        _risk_stats(norm_val[score_col], "Normal Validation")
        _risk_stats(atk[score_col],      "Attack Test")

    # ROC-AUC / PR-AUC on command rows (val + attack)
    try:
        from sklearn.metrics import roc_auc_score, average_precision_score
        eval_df = pd.concat([norm_val, atk], ignore_index=True)
        cmd_eval = eval_df[eval_df["event_type"] != "NONE"].copy()
        if len(cmd_eval) > 0 and cmd_eval["Normal/Attack"].nunique() == 2:
            y = (cmd_eval["Normal/Attack"] == "Attack").astype(int)
            for col in ["context_risk_max", "context_risk_weighted"]:
                roc = roc_auc_score(y, cmd_eval[col])
                apr = average_precision_score(y, cmd_eval[col])
                print(f"\n  {col}  ROC-AUC={roc:.4f}  PR-AUC={apr:.4f}  (command rows only)")
    except ImportError:
        print("\n  sklearn not available — ROC/PR-AUC skipped")
    except Exception as e:
        print(f"\n  ROC/PR-AUC error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# 11. METRICS FILES
# ──────────────────────────────────────────────────────────────────────────────

def generate_metrics(df: pd.DataFrame, calib_idx: list, valid_idx: list, attack_idx: list):
    norm_all  = df[df["Normal/Attack"] == "Normal"]
    atk_all   = df[df["Normal/Attack"] == "Attack"]
    norm_val  = df.loc[valid_idx]
    atk_test  = df.loc[attack_idx]

    norm_cmd = norm_all[norm_all["event_type"] != "NONE"]
    atk_cmd  = atk_all[atk_all["event_type"] != "NONE"]

    rows = []
    for col in ["context_risk_max", "context_risk_weighted"]:
        for t in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
            rows.append({
                "score":          col,
                "threshold":      t,
                "normal_rows_above":      int((norm_all[col] >= t).sum()),
                "attack_rows_above":      int((atk_all[col]  >= t).sum()),
                "normal_cmd_rows_above":  int((norm_cmd[col] >= t).sum()),
                "attack_cmd_rows_above":  int((atk_cmd[col]  >= t).sum()),
                "normal_fp_rate":         round((norm_all[col] >= t).mean() * 100, 3),
                "attack_det_rate":        round((atk_all[col]  >= t).mean() * 100, 3),
                "normal_mean":            round(norm_all[col].mean(), 4),
                "attack_mean":            round(atk_all[col].mean(), 4),
                "normal_median":          round(norm_all[col].median(), 4),
                "attack_median":          round(atk_all[col].median(), 4),
            })

    pd.DataFrame(rows).to_csv(OUT_METRICS, index=False)
    print(f"\nSaved metrics: {OUT_METRICS}")

    # Command metrics by type/phase
    cmd_rows = df[df["event_type"] != "NONE"].copy()
    cmd_metrics = (
        cmd_rows.groupby(["event_type", "process_phase", "Normal/Attack"])[
            ["context_risk_max", "context_risk_weighted",
             "risk_cmd_frequency", "risk_cmd_repetition",
             "risk_cmd_timing", "risk_command_sequence", "risk_phase_rarity"]
        ].mean().round(4).reset_index()
    )
    cmd_metrics.to_csv(OUT_CMD_MET, index=False)
    print(f"Saved command metrics: {OUT_CMD_MET}")

    # Threshold curve
    tc_rows = []
    for col in ["context_risk_max", "context_risk_weighted"]:
        for t in np.arange(0.1, 1.0, 0.1):
            t = round(t, 1)
            tc_rows.append({
                "score":        col,
                "threshold":    t,
                "normal_fpr":   round((norm_all[col] >= t).mean() * 100, 3),
                "attack_dr":    round((atk_all[col]  >= t).mean() * 100, 3),
            })
    pd.DataFrame(tc_rows).to_csv(OUT_THRESH, index=False)
    print(f"Saved threshold curve: {OUT_THRESH}")

    # Top examples
    cmd_rows_sorted = cmd_rows.sort_values("context_risk_max", ascending=False)
    top_n_atk  = cmd_rows_sorted[cmd_rows_sorted["Normal/Attack"] == "Attack"].head(50)
    top_n_norm = cmd_rows_sorted[cmd_rows_sorted["Normal/Attack"] == "Normal"].head(20)
    top_ex     = pd.concat([top_n_atk, top_n_norm], ignore_index=True)

    top_cols = [
        "Timestamp", "event_type", "event_value", "process_phase",
        "risk_cmd_frequency", "risk_cmd_repetition", "risk_cmd_timing",
        "risk_command_sequence", "risk_phase_rarity",
        "context_risk_max", "context_risk_weighted", "context_reason",
        "Normal/Attack",
    ]
    top_ex[[c for c in top_cols if c in top_ex.columns]].to_csv(OUT_TOPEX, index=False)
    print(f"Saved top examples: {OUT_TOPEX}")


# ──────────────────────────────────────────────────────────────────────────────
# 12. PLOTS
# ──────────────────────────────────────────────────────────────────────────────

def generate_plots(df: pd.DataFrame, valid_idx: list, attack_idx: list):
    norm_val = df.loc[valid_idx]
    atk      = df.loc[attack_idx]
    norm_all = df[df["Normal/Attack"] == "Normal"]
    atk_all  = df[df["Normal/Attack"] == "Attack"]

    # 1. Distribution
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, col in zip(axes, ["context_risk_max", "context_risk_weighted"]):
        ax.hist(norm_all[col], bins=50, alpha=0.6, density=True, color="steelblue", label="Normal")
        ax.hist(atk_all[col],  bins=50, alpha=0.6, density=True, color="crimson",   label="Attack")
        ax.set_title(f"Distribution: {col}")
        ax.set_xlabel("Risk Score")
        ax.set_ylabel("Density")
        ax.legend()
        ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "context_risk_distribution.png", dpi=150)
    plt.close()
    print("  Saved context_risk_distribution.png")

    # 2. Components
    cmd_rows = df[df["event_type"] != "NONE"].copy()
    comps = ["risk_cmd_frequency", "risk_cmd_repetition", "risk_cmd_timing",
             "risk_command_sequence", "risk_phase_rarity"]
    fig, axes = plt.subplots(len(comps), 1, figsize=(14, 12), sharex=True)
    colors = ["navy", "teal", "darkorange", "purple", "darkred"]
    for ax, comp, col in zip(axes, comps, colors):
        ax.plot(df.index, df[comp], color=col, linewidth=0.5, alpha=0.7, label=comp)
        ax.set_ylabel(comp.replace("risk_", ""), fontsize=8)
        ax.grid(alpha=0.2)
        ax.legend(loc="upper right", fontsize=7)
    axes[-1].set_xlabel("Row Index")
    plt.suptitle("V3 Risk Components Over Dataset", fontsize=12)
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "context_risk_components.png", dpi=150)
    plt.close()
    print("  Saved context_risk_components.png")

    # 3. Threshold curve
    norm_all_ser = norm_all["context_risk_max"]
    atk_all_ser  = atk_all["context_risk_max"]
    thresholds   = np.arange(0.05, 1.0, 0.05)
    fpr  = [(norm_all_ser >= t).mean() * 100 for t in thresholds]
    dr   = [(atk_all_ser  >= t).mean() * 100 for t in thresholds]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(thresholds, fpr, marker="o", color="steelblue", label="Normal FPR (%)")
    ax.plot(thresholds, dr,  marker="s", color="crimson",   label="Attack Detection Rate (%)")
    ax.set_xlabel("Threshold")
    ax.set_ylabel("Rate (%)")
    ax.set_title("Command-Row Threshold Curve (context_risk_max)")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "command_row_threshold_curve.png", dpi=150)
    plt.close()
    print("  Saved command_row_threshold_curve.png")

    # 4. Top risky commands
    cmd_rows_sorted = cmd_rows.nlargest(30, "context_risk_max")
    fig, ax = plt.subplots(figsize=(12, 6))
    colors_bar = ["crimson" if l == "Attack" else "steelblue" for l in cmd_rows_sorted["Normal/Attack"]]
    ax.bar(range(len(cmd_rows_sorted)), cmd_rows_sorted["context_risk_max"], color=colors_bar)
    ax.set_xticks(range(len(cmd_rows_sorted)))
    ax.set_xticklabels(
        [f"{r['event_type'][:18]}\n{str(r['Timestamp'])[:16]}" for _, r in cmd_rows_sorted.iterrows()],
        rotation=90, fontsize=6
    )
    ax.set_ylabel("context_risk_max")
    ax.set_title("Top 30 Highest-Risk Command Rows (red=Attack, blue=Normal)")
    ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "top_risky_commands.png", dpi=150)
    plt.close()
    print("  Saved top_risky_commands.png")

    print(f"\nAll plots saved to {PLOTS_DIR}/")


# ──────────────────────────────────────────────────────────────────────────────
# 13. SAVE OUTPUTS
# ──────────────────────────────────────────────────────────────────────────────

def save_outputs(df: pd.DataFrame, freq_model, rep_model, timing_model,
                 markov_model, phase_model, all_cmds, all_phases,
                 sparse_audit):
    # Output columns
    out_cols = [
        "Timestamp", "event_type", "event_value", "process_phase",
        "MV101_state", "P101_state", "P102_state",
        "command_frequency", "command_frequency_60s",
        "command_repetition", "consecutive_same_command",
        "time_since_last_command", "time_since_same_command",
        "risk_cmd_frequency", "risk_cmd_repetition",
        "risk_cmd_timing", "risk_command_sequence", "risk_phase_rarity",
        "context_risk_max", "context_risk_weighted",
        "context_level_max", "context_level_weighted",
        "context_reason", "Normal/Attack",
    ]
    existing_cols = [c for c in out_cols if c in df.columns]
    df[existing_cols].to_csv(OUT_CSV, index=False)
    print(f"\nSaved output CSV: {OUT_CSV}  ({len(df):,} rows)")

    # Save model artifacts as JSON
    artifacts = {
        "freq_model":    freq_model,
        "rep_model":     rep_model,
        "timing_model":  timing_model,
        "all_cmds":      all_cmds,
        "all_phases":    all_phases,
        "sparse_audit":  {k: (v if not isinstance(v, list) else [list(x) for x in v])
                          for k, v in sparse_audit.items()},
        "weights": {
            "frequency": WEIGHT_FREQ, "repetition": WEIGHT_REP,
            "timing": WEIGHT_TIM, "sequence": WEIGHT_SEQ, "phase": WEIGHT_PHASE
        },
        "laplace_k":      LAPLACE_K,
        "min_support":    MIN_SUPPORT,
        "calib_frac":     CALIB_FRAC,
    }
    # Markov model is too large for JSON directly — save summary
    artifacts["markov_summary"] = {
        "n_cmd_types":    len(all_cmds),
        "n_phases":       len(all_phases),
        "n_cmd_rows":     markov_model["n_cmd_rows"],
        "vocab_size":     markov_model["vocab_size"],
    }

    with open(ARTIFACTS / "v3_calibration.json", "w") as f:
        json.dump(artifacts, f, indent=2, default=str)
    print(f"Saved calibration artifacts: {ARTIFACTS / 'v3_calibration.json'}")


# ──────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ──────────────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()

    # 1. Load
    df = load_data()

    # 2. Timing features (causal, row-by-row)
    print("\nComputing causal timing features ...")
    df = create_command_features(df)

    # 3. Split
    calib_idx, valid_idx, attack_idx = create_calibration_split(df)

    # 4. Fit models
    freq_model   = fit_frequency_model(df, calib_idx)
    rep_model    = fit_repetition_model(df, calib_idx)
    timing_model = fit_timing_model(df, calib_idx)
    markov_model = fit_markov_model(df, calib_idx)
    phase_model, all_cmds, all_phases = fit_phase_rarity_model(df, calib_idx)

    # 5. Calculate risk
    df = calculate_risk_components(
        df, freq_model, rep_model, timing_model,
        markov_model, phase_model, all_cmds, all_phases
    )

    # 6. Candidate scores + levels
    df = calculate_candidate_scores(df)

    # 7. Context reason
    df = generate_context_reason(df)

    # 8. Sparse audit
    sparse_audit = sparse_transition_audit(df, markov_model)

    # 9. Evaluate
    evaluate_model(df, calib_idx, valid_idx, attack_idx)

    # 10. Metrics files
    generate_metrics(df, calib_idx, valid_idx, attack_idx)

    # 11. Plots
    print("\nGenerating plots ...")
    generate_plots(df, valid_idx, attack_idx)

    # 12. Save outputs
    save_outputs(df, freq_model, rep_model, timing_model,
                 markov_model, phase_model, all_cmds, all_phases,
                 sparse_audit)

    elapsed = time.time() - t0
    print(f"\n[DONE] Total runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
