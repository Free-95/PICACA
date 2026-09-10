# SWaT Stage 1 B1 Dataset Schema Specification

This document details the exact field definitions, sources, data types, physical units, and allowed value ranges for the Student 2 B1 Physics Handoff Dataset [`processed/swat_stage1_b1_input.csv`](file:///e:/ISA_PROJECT/processed/swat_stage1_b1_input.csv).

---

## Field Specifications

| Field Name | Data Type | Source Signal | Physical Unit | Allowed / Possible Values | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `timestamp` | `datetime64[ns]` | `Timestamp` | ISO Datetime | `2015-12-28 10:00:00` to `2016-01-02 14:59:59` | 1 Hz sampling recording timestamp |
| `command_type` | `string` | Discrete Event Extractor | Categorical | `NO_COMMAND`, `P101_ON`, `P101_OFF`, `P102_ON`, `P102_OFF`, `VALVE_OPEN`, `VALVE_CLOSE`, `VALVE_TRANSITION`, `P101_OFF;P102_ON`, etc. | Discrete actuator state change event trigger |
| `command_value` | `string` | Discrete Event Extractor | State Code | `NONE`, `1`, `0`, `Open`, `Closed`, `Transition`, `0;1` | New state value associated with command |
| `command_source` | `string` | System Architecture | Categorical | `NONE`, `AUTOMATIC_PLC` | Origin of command trigger |
| `LIT101` | `float64` | Raw SWaT Sensor | Millimeters ($mm$) | `189.83` to `925.03` | Raw level transmitter reading for tank T101 |
| `level_percent` | `float64` | Derived from `LIT101` | Percentage ($\%$) | `18.98%` to `92.50%` | Relative tank level ($LIT101 / 1000.0 \times 100$) |
| `recent_level_trend` | `float64` | Causal Difference | Millimeters ($mm$) | `-508.45` to `+639.82` | Causal 5-second level delta ($LIT101_t - LIT101_{t-5}$) |
| `P101_state` | `int64` | Raw Actuator `P101` | Binary State | `0` (OFF), `1` (ON) | Primary raw water pump operational state |
| `P102_state` | `int64` | Raw Actuator `P102` | Binary State | `0` (OFF), `1` (ON) | Backup raw water pump operational state |
| `pump_state` | `int64` | Derived State | Binary State | `0` (OFF), `1` (ON) | Active pump indicator ($P101_{state} \lor P102_{state}$) |
| `MV101_state` | `string` | Raw Actuator `MV101` | String Category | `Closed`, `Open`, `Transition` | Raw inlet valve state string |
| `valve_state` | `string` | Derived Category | String Category | `CLOSED`, `OPEN`, `TRANSITION` | Uppercase standardized valve state category |
| `FIT101` | `float64` | Raw SWaT Sensor | $m^3/h$ | `0.00` to `2.76` | Raw flow transmitter reading for Stage 1 inflow |
| `flow_value` | `float64` | `FIT101` | $m^3/h$ | `0.00` to `2.76` | Inflow rate equal to `FIT101` |
| `label` | `string` | SWaT Ground Truth | Categorical | `Normal`, `Attack` | Ground truth evaluation label |
