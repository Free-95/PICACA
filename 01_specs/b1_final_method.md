# Divija - Finalized B1 Physics-Only Baseline

## Objective

B1 is the physics-only baseline for PICACA-Lite. Its purpose is to decide whether a Stage 1 actuator command is physically safe using only the current SWaT process state, the tank level, the recent causal level trend, and basic sensor consistency checks. It does not use Yash's `context_risk`; that is reserved for B3 and B4.

## Inputs From Yash's B1 Handoff

The B1 engine consumes the schema in `processed/swat_stage1_b1_input.csv`:

- `timestamp`
- `command_type`
- `command_value`
- `LIT101`
- `level_percent`
- `recent_level_trend`
- `P101_state`
- `P102_state`
- `pump_state`
- `MV101_state`
- `valve_state`
- `FIT101`
- `flow_value`
- `label`

The ground-truth `label` is kept only for evaluation. It is not used while making a decision.

## Important SWaT Stage 1 Interpretation

For SWaT Stage 1, the engine must treat the actuators correctly:

- `VALVE_OPEN` / `MV101 Open` means water can enter tank T101. This can increase overflow risk.
- `P101_ON` or `P102_ON` means water is transferred out of T101. This can increase underflow risk, but it is protective when the tank is already high.
- `VALVE_CLOSE` reduces filling and is usually protective against overflow.
- `P101_OFF` / `P102_OFF` stops draining and is usually protective against underflow, but may increase overflow risk if the inlet valve remains open.

## B1 Method

The finalized B1 method is a causal, explainable rule-based physics baseline:

1. Convert Yash's trend into percent per second.
   - Yash's `recent_level_trend` is `LIT101(t) - LIT101(t-5)` in millimeters.
   - Since `level_percent = LIT101 / 1000 * 100`, the trend is converted before projection.

2. Classify the current tank level.
   - Low critical
   - Low warning
   - Normal
   - High warning
   - High critical

3. Classify the current trend.
   - Falling fast
   - Falling
   - Stable
   - Rising
   - Rising fast

4. Infer the physical effect of the command.
   - Filling
   - Draining
   - Stop filling
   - Stop draining
   - Neutral/no command

5. Project the tank level over a fixed short B1 lookahead.
   - B1 uses a fixed lookahead, currently 5 seconds.
   - This is intentionally different from B4, where lookahead will increase when context risk is high.

6. Calculate physical risk.
   - Filling commands are checked mainly for overflow risk.
   - Draining commands are checked mainly for underflow risk.
   - Protective commands are allowed unless sensor consistency is suspicious.

7. Check sensor consistency.
   - Large 5-second LIT101 jump is treated as possible spoofing.
   - Inflow active while level is falling is suspicious.
   - Pump active and valve closed while level is rising is suspicious.

8. Make the final decision.
   - `ALLOW`: physically safe command.
   - `DELAY_ALERT`: command is not directly blocked, but risk/sensor consistency requires operator alert.
   - `BLOCK_ALARM`: command can push the tank into unsafe physical state or sensor evidence is too unsafe.

## Initial Tuned Thresholds

These are the current starting thresholds:

| Parameter | Value | Reason |
|---|---:|---|
| Low critical | 20% | Tank is near unsafe low level |
| Low warning | 25% | Early warning before underflow |
| Normal low | 30% | Lower normal operating boundary |
| Normal high | 75% | Upper normal operating boundary |
| High warning | 80% | Early warning before overflow |
| High critical | 90% | Tank is near unsafe high level |
| B1 lookahead | 5 seconds | Matches Yash's causal 5-second trend window |
| Nominal fill rate | 0.05 %/s | Conservative Stage 1 filling assumption |
| Nominal drain rate | 0.05 %/s | Conservative Stage 1 draining assumption |
| Delay risk threshold | 0.55 | Medium-risk decision boundary |
| Block risk threshold | 0.85 | High-risk decision boundary |
| Sensor spike threshold | 50 mm / 5s | Captures unrealistic LIT101 jumps |

## Sample Schema Test

The engine was tested against Yash's committed sample file:

`github_picaca_lite/processed/swat_stage1_b1_sample20.csv`

Schema validation passed:

- Required columns found: yes
- Row count: 20

Dry-run B1 decision summary:

| Decision | Count |
|---|---:|
| ALLOW | 18 |
| DELAY_ALERT | 1 |
| BLOCK_ALARM | 1 |

The non-allow cases were:

- `2015-12-30 17:28:36`, `P101_OFF;P102_OFF`: delay/alert because inflow is active but the tank trend is falling.
- `2015-12-31 16:06:25`, `P101_OFF;P102_OFF`: block/alarm because the level is low-critical and the 5-second LIT101 jump is extremely large.

## Fine-Tuning Strategy

The tuning should be done in stages:

1. Start with safe engineering thresholds.
   - Use 20%, 25%, 75%, 80%, and 90% as initial boundaries.
   - This gives a defensible first version based on tank safety zones.

2. Run B1 on Yash's full `swat_stage1_b1_input.csv`.
   - Check how many normal rows become `DELAY_ALERT` or `BLOCK_ALARM`.
   - Normal false alarms should be low because B1 is a baseline.

3. Tune on normal data first.
   - Adjust normal/high/low warning boundaries only if normal operation creates too many alerts.
   - Do not use attack labels to make the system look artificially better.

4. Evaluate on attack data after fixing normal false alarms.
   - Use the label only after decisions are generated.
   - Report detection counts separately for command rows and all rows.

5. Tune one parameter at a time.
   - First tune tank zones.
   - Then tune lookahead.
   - Then tune trend thresholds.
   - Then tune sensor spike/contradiction thresholds.

6. Keep B1 intentionally simple.
   - B1 should be explainable and conservative.
   - B4 should be the smarter version where Yash's context risk changes the lookahead.

## What This Gives Divija

This B1 module is not just running a script. It is the physical decision baseline of the project:

- It interprets SWaT Stage 1 actuator commands physically.
- It projects future tank level from causal data.
- It separates overflow and underflow risk.
- It handles sensor consistency.
- It creates explainable `ALLOW`, `DELAY_ALERT`, and `BLOCK_ALARM` outputs.
- It becomes the comparison point for B3 and B4.
