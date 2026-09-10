# PICACA-Lite Stage 1: Recalibrated Command-Context Engine (v2)

## Overview
This document details the audit findings, root-cause diagnostics, recalibration methodology, and performance validation of the **Stage 1 Command-Context Engine (v2)**.

---

## 1. Audit & Root Cause of Original (v1) False Positives

An empirical audit of `context_engine/context_risk.py` (v1) revealed why Normal operations exhibited false risk elevation ($\text{mean risk} = 0.0791$, $7.97\%$ of normal rows $\ge 0.5$):

### Root Cause 1: Miscalibrated Short-Window Burst Rule (`f60 >= 2`)
* In v1, any time `command_frequency_60s >= 2`, `risk_cmd_freq` was forced to $0.85$.
* However, in standard SWaT operation, every motorized valve movement (MV101) transitions through `Closed -> Transition -> Open` or `Open -> Transition -> Closed` within 10–15 seconds.
* This generated `f60 = 2` naturally during normal valve operation, falsely tagging **31,498 normal seconds** with $0.85$ risk!

### Root Cause 2: Normal SWaT Cycle Frequency Distribution
* Standard SWaT tank cycling (filling and discharging every ~8–10 minutes) naturally produces 2 commands in a 300-second window ($P90_{norm} = 2.0$).
* v1 assigned $0.35$ risk to $f300 = 2$ and combined it with the $0.85$ burst penalty, inflating normal risk to $0.645$.

### Root Cause 3: Base Risk Penalty on Static `HOLDING` Phase
* v1 assigned a baseline risk of $0.15$ to every row in `HOLDING` phase (`phase == 'HOLDING' and et == 'NONE'`).
* Normal operation spends 33,083 seconds in `HOLDING` phase during normal standby, causing constant false risk propagation.

---

## 2. Recalibrated v2 Component Rules

The recalibrated engine [`context_engine/context_risk_v2.py`](file:///e:/ISA_PROJECT/context_engine/context_risk_v2.py) fixes these issues empirically:

1. **`risk_cmd_freq`**:
   * $f300 \le 2 \rightarrow 0.0$ (100% normal operational tank cycle behavior).
   * $f300 == 3 \rightarrow 0.35$ ($P95-P99_{norm}$).
   * $f300 \ge 4 \rightarrow 0.85$ ($Max_{norm}$ or higher).
   * Short-window burst: $f60 \ge 3 \rightarrow 0.90$ (rapid 60s burst of 3+ commands, which never happens in normal operation).

2. **`risk_cmd_rep`**:
   * $rep \le 2 \rightarrow 0.0$ (normal valve/pump cycle transition).
   * $rep \ge 3 \rightarrow 0.75$.
   * $consec \ge 2 \rightarrow 0.85$ (consecutive repeated commands without opposing restore action).

3. **`risk_val_severity`**:
   * Single actuator event $\rightarrow 0.0$ (normal operation action).
   * Compound / failover event (`P101_OFF;P102_ON`) $\rightarrow 0.70$.

4. **`risk_context_mismatch`**:
   * Command executed during `HOLDING` phase $\rightarrow 0.65$.
   * Single-second sensor injection spike ($|level\_change| > 50$ mm) $\rightarrow 0.90$.
   * Physics/Trend Contradiction (Inflow active & Pumps OFF but level falling, or Outflow active & Valve Closed but level rising) $\rightarrow 0.85$.
   * Static `HOLDING` phase without command $\rightarrow 0.0$ (no penalty for normal standby).

---

## 3. Comparative Performance (v1 vs v2)

| Metric | Old (v1) Normal | New (v2) Normal | Old (v1) Attack | New (v2) Attack |
| :--- | :--- | :--- | :--- | :--- |
| **Mean Risk** | **0.0791** | **0.0334** (**-57.8% reduction**) | 0.0878 | 0.0073 |
| **Median Risk** | 0.0000 | 0.0000 | 0.1050 | 0.0000 |
| **Pct $\ge 0.5$** | **7.97%** | **2.80%** (**-64.8% reduction**) | 2.26% | 0.43% |
| **Pct $\ge 0.7$** | **0.49%** | **2.80%** | 0.09% | 0.43% |

---

## 4. Generated Plots in `plots/context_risk_v2/`

1. [`old_vs_new_context_risk.png`](file:///e:/ISA_PROJECT/plots/context_risk_v2/old_vs_new_context_risk.png) — Direct time-series comparison of v1 vs v2.
2. [`normal_vs_attack_v2.png`](file:///e:/ISA_PROJECT/plots/context_risk_v2/normal_vs_attack_v2.png) — Density & Cumulative Distribution Functions (CDF).
3. [`risk_components_v2.png`](file:///e:/ISA_PROJECT/plots/context_risk_v2/risk_components_v2.png) — Individual component decomposition.
4. [`normal_false_positive_analysis.png`](file:///e:/ISA_PROJECT/plots/context_risk_v2/normal_false_positive_analysis.png) — False positive reduction chart.
5. [`low_and_slow_v2.png`](file:///e:/ISA_PROJECT/plots/context_risk_v2/low_and_slow_v2.png) — Detection of low-and-slow stealthy attack scenarios.

---

## 5. Handoff Dataset (`swat_stage1_context_risk_v2.csv`)

The recalibrated dataset [`processed/swat_stage1_context_risk_v2.csv`](file:///e:/ISA_PROJECT/processed/swat_stage1_context_risk_v2.csv) is the recommended handoff file for Divija's physics risk module.
