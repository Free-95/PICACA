# Student 2 (Divija) B1 Physics Input Handoff README

## 1. Dataset Purpose
This package provides the cleaned, validated 1-second Stage 1 physical and process input dataset for Student 2 (Divija) to build the B1 Physics-Only module (tank zones, safety margins, projected levels, lookahead, and physics risk).

## 2. Source Dataset
* **Source**: Cleaned SWaT Stage 1 time series ([`processed/swat_stage1_clean.csv`](file:///e:/ISA_PROJECT/processed/swat_stage1_clean.csv)).
* **Handoff File**: [`processed/swat_stage1_b1_input.csv`](file:///e:/ISA_PROJECT/processed/swat_stage1_b1_input.csv)
* **Sample File**: [`processed/swat_stage1_b1_sample20.csv`](file:///e:/ISA_PROJECT/processed/swat_stage1_b1_sample20.csv) (20 representative rows)

## 3. Row Count & Integrity
* **Total Rows**: Exactly **449,919**
* **Normal Rows**: **395,298**
* **Attack Rows**: **54,621**
* **Missing Values**: 0 across all columns.

## 4. Sampling Rate & Timestamps
* **Sampling Interval**: 1 second (1 Hz).
* **Time Range**: `2015-12-28 10:00:00` to `2016-01-02 14:59:59`.
* **Ordering**: Strictly monotonic increasing timestamp order.

## 5. Physical Signals
* `LIT101`: Raw level transmitter reading for tank T101 (range: `189.83` to `925.03` mm).
* `FIT101` / `flow_value`: Inflow rate (range: `0.00` to `2.76` $m^3/h$).

## 6. Command Representation
* `command_type`: Discrete actuator state change event (`NO_COMMAND`, `P101_ON`, `P101_OFF`, `VALVE_OPEN`, `VALVE_CLOSE`, `VALVE_TRANSITION`, or compound `P101_OFF;P102_ON`).
* `command_value`: Associated state value (`1`, `0`, `Open`, `Closed`, `Transition`, `NONE`, or compound `0;1`).
* `command_source`: `AUTOMATIC_PLC` or `NONE`.

## 7. Level Representation
* `LIT101`: Unmodified raw sensor reading in millimeters ($mm$).
* `level_percent`: Level expressed as percentage of full 1000mm tank height ($LIT101 / 1000.0 \times 100.0$).

## 8. Trend Calculation
* `recent_level_trend`: Strictly causal 5-second level difference ($LIT101_t - LIT101_{t-5}$).
  * Positive values indicate level is rising ($mm$).
  * Negative values indicate level is falling ($mm$).
  * Near-zero indicates stable tank level.
  * No future lookahead or `bfill` was used.

## 9. Pump-State Logic
* `P101_state` (`0` = OFF, `1` = ON)
* `P102_state` (`0` = OFF, `1` = ON)
* `pump_state`: `1` if `P101_state == 1` OR `P102_state == 1` else `0`.

## 10. Valve-State Logic
* `MV101_state`: Raw string (`'Open'`, `'Closed'`, `'Transition'`).
* `valve_state`: Standardized string (`'OPEN'`, `'CLOSED'`, `'TRANSITION'`).

## 11. Label Mapping
* `label`: Ground truth label (`'Normal'` / `'Attack'`).
* Labels are provided strictly for evaluation; no label is used as an input feature.

## 12. Example Python Usage for Student 2

```python
import pandas as pd

# Load B1 physical handoff dataset
df = pd.read_csv("processed/swat_stage1_b1_input.csv")

# Extract physical inputs for physics risk & lookahead engine
timestamp   = df['timestamp']
level_mm    = df['LIT101']
level_pct   = df['level_percent']
trend_5s    = df['recent_level_trend']
inflow      = df['FIT101']
pump_on     = df['pump_state']
valve_state = df['valve_state']
```
