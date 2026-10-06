# PICACA-Lite Stage 1: Command-Context Engine Documentation

## Overview
This module implements the **Command-Context Engine** for Stage 1 of the PICACA-Lite project (Raw Water Tank T101, Flow Transmitter FIT101, Motorized Valve MV101, and Pumps P101/P102).

The module converts cleaned 1-second SWaT sensor/actuator time series into discrete command event series, builds 300-second sliding-window context features, calibrates normal operational baselines, and calculates a unified **`context_risk` score $\in [0.0, 1.0]$**.

---

## Architecture & Data Flow

```text
RAW SWaT Data (normal.csv / attack.csv)
       │
       ▼
[swat_cleaner.py] ───► processed/swat_stage1_clean.csv
       │
       ▼
[swat_event_extractor.py] ───► processed/swat_stage1_events.csv
       │
       ▼
[swat_context_features.py] ───► processed/swat_stage1_context_features.csv
       │
       ▼
[calibrate_baseline.py] ───► context_engine/baseline/normal_baseline_stats.json
       │
       ▼
[context_engine/context_risk.py] ───► processed/swat_stage1_context_risk.csv
```

---

## Features Provided in Handoff Output

The output file [`processed/swat_stage1_context_risk.csv`](file:///e:/ISA_PROJECT/processed/swat_stage1_context_risk.csv) contains 24 columns designed for seamless consumption by Divija's physics risk and decision engine:

### 1. Process & Sensor Fields (Primary Inputs for Physics Engine)
* `Timestamp`: Datetime ISO timestamp (1 Hz sampling rate).
* `LIT101`: Raw water tank level in mm.
* `FIT101`: Water inflow rate in $m^3/h$.
* `P101_state`: Primary pump state binary (`0` = OFF, `1` = ON).
* `P102_state`: Backup pump state binary (`0` = OFF, `1` = ON).
* `MV101_state`: Inlet motorized valve state (`'Closed'`, `'Open'`, `'Transition'`).

### 2. Physical Dynamics & Trend Features
* `level_change`: 1-second tank level delta ($LIT101_t - LIT101_{t-1}$) in mm.
* `level_slope`: Instantaneous level slope ($mm/s$).
* `level_slope_300s`: Rolling 300-second average level slope ($mm/s$).
* `robust_level_slope`: **Rolling 300-second median slope** (robust against single-second sensor injection spikes up to $\pm 500$ mm).
* `level_trend_direction`: Trend category (`'RISING'`, `'FALLING'`, `'STABLE'`).
* `process_phase`: Physical phase (`'FILLING'`, `'DRAINING'`, `'TRANSFERRING'`, `'HOLDING'`, `'TRANSITIONING'`).

### 3. Command Behaviour & Frequency Features
* `event_type`: Discrete event trigger (`'P101_ON'`, `'P101_OFF'`, `'P102_ON'`, `'P102_OFF'`, `'MV101_CHANGE'`, or compound `'P101_OFF;P102_ON'`; `'NONE'` if no transition).
* `event_value`: New state value corresponding to event_type.
* `command_frequency`: Number of discrete command events in the past 300-second window ($W=300$).
* `command_frequency_60s`: Number of discrete command events in the past 60-second window ($W_{short}=60$).
* `command_repetition`: Max repetitions of any single command type in the past 300s window.
* `consecutive_same_command`: Count of consecutive identical commands without an opposing restore action.
* `command_value_change`: Event severity code (`0` = None, `1` = Standard Single Command, `2` = Compound / Failover Event).

### 4. Calibrated Context Risk Scores
* `risk_cmd_freq`: Frequency anomaly score $\in [0, 1]$.
* `risk_cmd_rep`: Repetition & accumulation score $\in [0, 1]$.
* `risk_val_severity`: Command value change severity score $\in [0, 1]$.
* `risk_context_mismatch`: Process phase & trend mismatch score $\in [0, 1]$.
* **`context_risk`**: **Unified Command-Context Risk Score $\in [0.0, 1.0]$**.

---

## Baseline Calibration Methodology
* Baseline statistics were calibrated strictly on 395,298 seconds (~4.57 days) of clean normal operation data (`swat_stage1_normal.csv`).
* Calibrated thresholds (P50, P75, P90, P95, P99, Max) are stored in [`context_engine/baseline/normal_baseline_stats.json`](file:///e:/ISA_PROJECT/context_engine/baseline/normal_baseline_stats.json).
* Attack data was **never** used during calibration; it is used only for evaluation.

---

## Context-Risk Calculation Formula
Component scores ($r_1 = risk\_cmd\_freq, r_2 = risk\_cmd\_rep, r_3 = risk\_val\_severity, r_4 = risk\_context\_mismatch$) are combined using a hybrid max-mean aggregation function:

$$\text{context\_risk} = \text{clip}\left(0.60 \times \max(r_1, r_2, r_3, r_4) + 0.40 \times \frac{r_1 + r_2 + r_3 + r_4}{4}, \; 0.0, \; 1.0\right)$$

This ensures:
1. High sensitivity if *any* single component exhibits severe anomaly.
2. Accumulative sensitivity for "low-and-slow" stealthy attacks where multiple subtle context anomalies persist over time.

---

## How to Run the Module

Execute from the workspace root:

```bash
# 1. Clean Stage 1 data
python swat_cleaner.py

# 2. Extract discrete events & dynamics
python swat_event_extractor.py

# 3. Engineer context features
python swat_context_features.py

# 4. Calibrate normal baseline statistics
python calibrate_baseline.py

# 5. Compute context risk & generate handoff dataset
python context_engine/context_risk.py
```

---

## Handoff Guide for Divija's Physics Engine

Divija's physics risk module can directly read [`processed/swat_stage1_context_risk.csv`](file:///e:/ISA_PROJECT/processed/swat_stage1_context_risk.csv) and extract:
```python
import pandas as pd

df = pd.read_csv("processed/swat_stage1_context_risk.csv")

# Extract inputs for physics risk & lookahead engine
timestamp = df['Timestamp']
level = df['LIT101']
robust_trend = df['robust_level_slope']
flow = df['FIT101']
pump_state = df['P101_state']
context_risk = df['context_risk'] # Yash's context risk score [0, 1]
```
