# B1 Physics-Only Baseline: Final Report Section

## Purpose

B1 is the physics-only baseline of PICACA-Lite. It checks whether a Stage 1 command is physically safe for the SWaT raw water tank using only process values such as tank level, recent level trend, pump state, valve state, and flow. It does not use Yash's `context_risk`; that is kept for B3/B4.

The main question B1 answers is:

> If this command is allowed, will the tank move toward overflow, underflow, or sensor-inconsistent behavior within a short fixed horizon?

## Input Data

B1 uses the full handoff dataset generated from the SWaT raw files:

```text
processed/swat_stage1_b1_input.csv
```

This file contains 449,919 rows:

| Label | Rows |
|---|---:|
| Normal | 395,298 |
| Attack | 54,621 |

The important input fields are:

- `timestamp`
- `command_type`
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

The `label` field is used only for evaluation after decisions are generated. It is not used by the decision logic.

## Method

B1 first converts Yash's `recent_level_trend` into percent per second. In the B1 handoff file, the trend is a causal 5-second change in millimeters:

```text
recent_level_trend = LIT101(t) - LIT101(t-5)
```

Since `level_percent = LIT101 / 1000 * 100`, the engine converts this trend before doing projection.

B1 then classifies the current tank level:

- low critical
- low warning
- normal
- high warning
- high critical

It also classifies the trend:

- falling fast
- falling
- stable
- rising
- rising fast

Then it interprets the command physically:

| Command Type | Physical Meaning |
|---|---|
| `VALVE_OPEN` / `VALVE_TRANSITION` toward open | Filling risk |
| `VALVE_CLOSE` | Reduces overflow risk |
| `P101_ON` / `P102_ON` | Drains/transfers water out of T101 |
| `P101_OFF` / `P102_OFF` | Stops draining |
| `NO_COMMAND` | Monitoring row only |

B1 uses a fixed 5-second lookahead. This is intentionally different from B4, where the lookahead will become adaptive using context risk.

## Final Frozen B1 Configuration

The final configuration is saved in:

```text
configs/b1_final_config.json
```

| Parameter | Final Value | Reason |
|---|---:|---|
| Low critical | 20% | Lower physical safety boundary |
| Low warning | 25% | Warning before underflow |
| Normal low | 30% | Lower normal operating boundary |
| Normal high | 75% | Upper normal operating boundary |
| High warning | 80% | Warning before overflow |
| High critical | 90% | Upper physical safety boundary |
| Fixed lookahead | 5 seconds | Matches the causal 5-second trend window |
| Nominal fill rate | 0.05 %/s | Close to normal median filling rate |
| Nominal drain rate | 0.05 %/s | Close to normal median draining rate |
| Sensor spike threshold | 50 mm / 5s | Captures extreme sensor jumps |
| Contradictory trend threshold | 0.0887 %/s | Normal 99th percentile absolute trend |
| Delay threshold | 0.55 | Medium risk boundary |
| Block threshold | 0.85 | High risk boundary |

The contradictory trend threshold was tuned from normal data, so it is not arbitrary. The normal 99th percentile absolute trend was approximately `0.088712 %/s`, so the final threshold was frozen as `0.0887 %/s`.

## Lookahead Selection

To make sure the 5-second B1 lookahead was not chosen blindly, an extended sweep was run from 1 second to 60 seconds while keeping all other thresholds fixed.

Key rows from the sweep:

| Lookahead | Normal Non-Allow | Attack Non-Allow | Normal Command False Alert Rate | Attack Command Detection Rate |
|---:|---:|---:|---:|---:|
| 1s | 3 | 4 | 0.427960% | 10.256410% |
| 3s | 3 | 4 | 0.427960% | 10.256410% |
| 5s | 3 | 4 | 0.427960% | 10.256410% |
| 10s | 3 | 4 | 0.427960% | 10.256410% |
| 15s | 4 | 4 | 0.570613% | 10.256410% |
| 30s | 4 | 4 | 0.570613% | 10.256410% |
| 60s | 62 | 7 | 8.844508% | 17.948718% |

The sweep shows that 1s, 3s, 5s, and 10s produce the same command-row detection, while longer horizons start increasing normal false alerts. The 5-second lookahead was kept because it matches the 5-second causal trend feature in Yash's handoff file and gives stable results without adding unnecessary false alerts.

Generated evidence:

```text
processed/b1_lookahead_sweep_1_to_60.csv
plots/b1_final/b1_lookahead_sweep_1_to_60.png
```

## Tuning Result

Before tuning, B1 generated more normal delay alerts because small normal process mismatches were treated as suspicious. After normal-only calibration, the false alerts reduced significantly.

Default B1:

| Label | Allow | Delay Alert | Block Alarm |
|---|---:|---:|---:|
| Normal | 395,276 | 20 | 2 |
| Attack | 54,614 | 4 | 3 |

Final tuned B1:

| Label | Allow | Delay Alert | Block Alarm |
|---|---:|---:|---:|
| Normal | 395,295 | 1 | 2 |
| Attack | 54,617 | 1 | 3 |

Command rows only after tuning:

| Label | Allow | Delay Alert | Block Alarm |
|---|---:|---:|---:|
| Normal | 698 | 1 | 2 |
| Attack | 35 | 1 | 3 |

## Evaluation Metrics

The SWaT attack label covers entire attack periods, including many rows where no new actuator command is issued. Therefore, B1 should not be judged by expecting every attack-labeled row to be blocked. B1 is a command authorization baseline, so the most useful view is command rows only.

Final B1 metrics:

| Metric | Scope | Result | Meaning |
|---|---|---:|---|
| Normal false alert rate | All rows | 0.000759% (3 / 395,298) | B1 almost never interrupts normal operation |
| Attack flag rate | All rows | 0.007323% (4 / 54,621) | B1 flags only physically obvious attack-period rows |
| Normal command false alert rate | Command rows only | 0.427960% (3 / 701) | Very few normal commands are delayed or blocked |
| Attack command detection rate | Command rows only | 10.256410% (4 / 39) | B1 catches the strongest physics/sensor attack commands |

This result is expected for a physics-only baseline. B1 is very good at avoiding false alarms, but it is limited against low-and-slow or context-based attacks. That limitation is not a failure of B1; it is the reason B4 is needed.

Generated plots:

```text
plots/b1_final/b1_final_metric_rates.png
plots/b1_final/b1_final_all_rows_percent.png
plots/b1_final/b1_final_command_rows_percent.png
plots/b1_final/b1_final_command_counts.png
```

The most important graph is `b1_final_metric_rates.png`, because it shows the tradeoff clearly: normal false alerts are almost zero, while attack command detection is limited because B1 does not use context risk.

## Example Decisions

Example 1: attack row blocked due to severe sensor/physics risk.

```text
Timestamp: 2015-12-31 16:06:25
Command: P101_OFF;P102_OFF
Level: 19.3398%
Trend: -10.13203 %/s
Decision: BLOCK_ALARM
Reason: Large 5-second LIT101 jump suggests possible sensor spoofing; B1 physical risk is high.
```

Example 2: attack row delayed because level was already in high warning.

```text
Timestamp: 2015-12-28 10:44:33
Command: VALVE_TRANSITION
Level: 87.1058%
Projected level: 87.1725%
Decision: DELAY_ALERT
Reason: B1 physical risk is medium; delay and alert operator.
```

Example 3: normal protective command allowed.

```text
Timestamp: 2015-12-28 10:21:47
Command: VALVE_CLOSE
Level: 80.5977%
Decision: ALLOW
Reason: Valve close reduces overflow risk.
```

## Interpretation

The tuned B1 baseline is intentionally conservative and explainable. It does not try to detect every attack because it only uses physics. Its purpose is to provide a fair baseline for comparison against B3 and B4.

B1 successfully catches the strongest physical/sensor-risk cases, especially large level jumps and unsafe projected tank states. However, attacks that are contextually suspicious but physically subtle may still pass B1. That is expected and useful, because B4 is designed to improve exactly this gap by adding context-conditioned lookahead.

## Possible Improvements

There are three useful improvements we can make later:

1. Add a configuration-driven tuning workflow permanently.
   - This is already started through `configs/b1_final_config.json`.
   - Future runs can change thresholds without editing source code.

2. Add evaluation plots.
   - A confusion-style decision bar chart.
   - A level/time plot marking B1 delay and block events.
   - A command-row-only evaluation chart.

3. Add a calibration report generator.
   - It can automatically export normal percentiles, final thresholds, and before/after tuning tables for the final report.

For now, the core B1 baseline is complete enough to serve as the physics-only comparison point for B3 and B4.
