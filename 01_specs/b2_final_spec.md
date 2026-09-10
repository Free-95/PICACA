# B2 Final Specification: State-Aware Residual Dynamics Model

## 1. Purpose

B2 is the learned normal-dynamics layer of PICACA-Lite.

Its purpose is to answer one question:

> Given the current actuator state and flow condition, is the tank level changing the way it normally changes?

B2 does not make the final safety decision. It does not output `ALLOW`, `DELAY_ALERT`, or `BLOCK_ALARM`. Instead, it outputs a dynamics-based risk score:

```text
model_risk
model_level
model_reason
```

This risk will later be used by B3 and B4 along with B1 physics risk and Yash's context risk.

## 2. Clean Boundary

The work is divided as follows:

| Module | Main Question | Owner |
|---|---|---|
| Yash Context Risk | Is the command context suspicious? | Yash |
| B1 Physics Baseline | Will the tank become physically unsafe after a fixed 5-second lookahead? | Divija |
| B2 Normal Dynamics | Is the tank movement normal for this actuator-state and flow condition? | Divija |
| B3 Fusion | Does combining B1, B2, and context risk improve detection? | Divija |
| B4 PICACA | Can context risk adapt the physics lookahead and final decision? | Divija |

B2 must not implement:

```text
projected tank level
overflow or underflow decision
adaptive lookahead
command frequency risk
command sequence risk
final allow/delay/block decision
```

B2 only checks whether observed tank movement is consistent with learned normal behavior.

Shared command-row definition:

```text
command row = event_type != "NONE"
```

This must match Yash's context-risk metrics and the B1/B3/B4 comparison tables.

## 3. Input File

B2 uses the context feature output already generated from SWaT Stage 1:

```text
processed/swat_stage1_context_features.csv
```

Required columns:

```text
Timestamp
LIT101
FIT101
MV101_state
P101_state
P102_state
process_phase
event_type
Normal/Attack
```

The `Normal/Attack` label is used only for calibration split and final evaluation. It must not be used inside the risk formula.

## 4. Shared Process Phase Definition

The process phase must be actuator-based only. It must not be derived from level movement or slope, otherwise B2 becomes circular.

| Condition | process_phase |
|---|---|
| `MV101_state == Transition` | `TRANSITIONING` |
| `MV101_state == Open` and both pumps OFF | `FILLING` |
| `MV101_state == Closed` and any pump ON | `DRAINING` |
| `MV101_state == Open` and any pump ON | `TRANSFERRING` |
| `MV101_state == Closed` and both pumps OFF | `HOLDING` |
| otherwise | `UNKNOWN` |

## 5. State Key

B2 must condition on the actual actuator-state tuple, not only the readable process phase.

Create:

```text
state_key = MV101_state + "_" + P101_state + "_" + P102_state
```

Examples:

```text
Open_0_0
Closed_1_0
Open_1_0
Closed_0_0
Transition_0_0
```

The `process_phase` is kept as a readable label, but the envelope is learned using:

```text
state_key x flow_bin
```

## 6. Flow Bin

FIT101 is used only as a conditioning input. It is not treated as a separate anomaly target.

Create flow bins from calibration-normal data:

```text
LOW_FLOW
MEDIUM_FLOW
HIGH_FLOW
```

Recommended method:

```text
flow_epsilon = P95(FIT101) over calibration-normal rows where process_phase is HOLDING or DRAINING
flowing_rows = calibration-normal rows where FIT101 > flow_epsilon

LOW_FLOW    = FIT101 <= flow_epsilon
MEDIUM_FLOW = flow_epsilon < FIT101 <= flowing_rows P50
HIGH_FLOW   = FIT101 > flowing_rows P50
```

Note:

> Flow bins must not be calculated from all normal rows globally, because most DRAINING and HOLDING rows have `FIT101` near zero and would collapse the percentile thresholds. The bin thresholds should be learned from flowing calibration rows, or within FILLING/TRANSFERRING rows. Draining states may mostly fall under `LOW_FLOW`, which is expected because FIT101 measures inlet flow.

## 7. Robust 5-Second Movement

B2 should not use raw endpoint differencing directly because it can amplify noise.

Use a causal rolling median. The SWaT Stage 1 file is expected to be 1 Hz, so a 5-second change horizon equals 5 samples. To reduce noise, the smoothing window may be slightly wider than the change horizon:

```text
smooth_LIT101 = rolling median of LIT101 over the last 7 to 9 samples
robust_change_5s_percent = (smooth_LIT101(t) - smooth_LIT101(t-5)) / 1000 * 100
```

If the file is resampled later, define both windows in samples explicitly before running B2.

All envelopes and risks must be computed on:

```text
robust_change_5s_percent
```

not raw LIT101 and not raw millimeter change.

## 8. Boundary Window Exclusion

Rows immediately after actuator or phase changes should not be scored because the past 5-second movement may still belong to the previous state.

Do not score rows within the boundary exclusion window after:

```text
event_type != "NONE"
```

or after a `process_phase` change.

The boundary exclusion window must be at least as long as both the 5-second change horizon and the smoothing window:

```text
boundary_exclusion_samples = max(5, smoothing_window_samples)
```

For example, if the smoothing window is 9 samples, exclude the first 9 samples after an actuator event or phase change. This prevents a scored smoothed value from mixing pre-change and post-change states.

For these rows:

```text
scored_flag = False
model_risk = 0.0
model_level = NOT_SCORED
model_reason = "Not scored because row is inside actuator/phase boundary window."
```

B3/B4 should treat `model_risk = 0.0` as neutral, but the CSV must still show `model_level = NOT_SCORED`.

## 9. Normal-Only Calibration Split

B2 must use normal data only for learning.

Split normal rows chronologically:

```text
first 80% normal rows = calibration/training
last 20% normal rows = validation
attack rows = final evaluation only
```

Use calibration rows to learn:

```text
flow-bin thresholds
state-flow envelopes
direction deadband
tail-width behavior
```

Use validation rows to check false alerts and adjust allowed parameters.

Use attack rows only after freezing the model.

## 10. Minimum Support Rule

For each group:

```text
state_key x flow_bin
```

calculate calibration support.

Hard minimum rule:

```text
if support_count < 100:
    do not score the group
```

Rows from that group are marked:

```text
scored_flag = False
model_risk = 0.0
model_level = NOT_SCORED
model_reason = "Not scored because calibration support is too low for this state-flow group."
```

Preferred support for stable P1/P99 envelope estimation:

```text
support_count >= 300
```

Groups with support between 100 and 299 may be scored, but they should be watched during validation and reported separately.

## 11. Normal Envelope

For each valid `state_key x flow_bin` group, compute the following from calibration rows:

```text
median_change
P1
P5
P95
P99
support_count
```

All values are computed on:

```text
robust_change_5s_percent
```

Default envelope:

```text
normal envelope   = P5 to P95
critical envelope = P1 to P99
```

If validation false alerts are too high, do not widen only the normal envelope. If widening is used, widen both envelopes together:

```text
normal envelope:   P5/P95 -> P2.5/P97.5
critical envelope: P1/P99 -> P0.5/P99.5
```

## 12. Tail-Width Floor

Low-variance groups can make the exceedance denominator too small. This would make tiny excursions produce risk 1.0.

Use:

```text
tail_width_floor = 0.02%
```

So:

```text
lower_tail_width = max(lower_normal - lower_critical, 0.02)
upper_tail_width = max(upper_critical - upper_normal, 0.02)
```

If a group is too narrow and unstable during validation, mark that group as `NOT_SCORED` rather than trusting a fragile envelope.

## 13. Direction Deadband

Do not hardcode the stable-direction deadband.

Calculate it from calibration rows of genuinely stable groups, mainly:

```text
Closed_0_0 / HOLDING
```

Use:

```text
deadband = P95(abs(robust_change_5s_percent)) from stable calibration rows
```

This prevents sensor noise from being treated as false wrong-direction movement.

## 14. Residual Formula

For every scored row:

```text
actual = robust_change_5s_percent
expected = median_change
lower_normal = P5
upper_normal = P95
lower_critical = P1
upper_critical = P99
```

Calculate:

```text
dynamics_residual = actual - expected
```

Then calculate exceedance:

```text
if actual < lower_normal:
    exceedance = (lower_normal - actual) / lower_tail_width

elif actual > upper_normal:
    exceedance = (actual - upper_normal) / upper_tail_width

else:
    exceedance = 0
```

Then:

```text
base_risk = clip(exceedance, 0, 1)
```

## 15. Direction Logic

Define direction using the learned deadband:

```text
if abs(value) <= deadband:
    direction = "STABLE"
elif value > 0:
    direction = "RISING"
else:
    direction = "FALLING"
```

Use the same function for both expected and actual movement:

```text
expected_direction = direction(median_change)
actual_direction = direction(actual)
```

Stable-compatible means:

```text
expected_direction == STABLE
```

or:

```text
actual_direction == STABLE
```

Wrong-way movement means:

```text
expected_direction = RISING and actual_direction = FALLING
```

or:

```text
expected_direction = FALLING and actual_direction = RISING
```

## 16. Final Model Risk

Use this explicit branch order:

```text
if wrong_direction and base_risk > 0:
    model_risk = 0.60 + 0.40 * base_risk
elif wrong_direction and base_risk == 0:
    model_risk = 0.0
else:
    model_risk = base_risk
```

So opposite-direction movement is only penalized when it also falls outside the learned normal envelope.

Then:

```text
model_risk = clip(model_risk, 0, 1)
```

This allows same-direction stealth attacks, such as very slow filling, to still reach high risk if they are far outside the learned envelope.

Validation safeguard:

> If same-direction `VERY_HIGH` risk is too frequent on held-out normal validation rows, cap same-direction risk at `0.85`. Apply this only if validation shows inflated false alerts.

## 17. Stable-Expected Group Safeguard

This safeguard applies to any group whose learned expected direction is `STABLE`, not only the `HOLDING` phase. This includes classic holding groups and any balanced transferring group where normal inflow and outflow nearly cancel.

The most important stable group is:

```text
MV101_state = Closed
P101_state = 0
P102_state = 0
```

but the rule should generalize to:

```text
expected_direction = STABLE
```

During validation, confirm:

```text
abs(P5) < 0.05%
abs(P95) < 0.05%
```

If the stable-expected envelope is tight, normal envelope scoring is enough.

If the stable-expected envelope is loose, add:

```text
if expected_direction == STABLE and abs(actual) > stable_movement_limit:
    model_risk = max(model_risk, 0.60)
```

where `stable_movement_limit` is learned from stable calibration rows.

## 18. Risk Levels

For scored rows:

| model_risk range | model_level |
|---|---|
| `0.00 <= model_risk < 0.30` | `LOW` |
| `0.30 <= model_risk < 0.60` | `MEDIUM` |
| `0.60 <= model_risk < 0.85` | `HIGH` |
| `0.85 <= model_risk <= 1.00` | `VERY_HIGH` |

For unscored rows:

```text
model_level = NOT_SCORED
```

## 19. Output File

B2 should generate:

```text
processed/swat_b2_dynamics_results.csv
```

Required columns:

```text
Timestamp
Normal/Attack
event_type
process_phase
state_key
flow_bin
support_count
exceedance
expected_direction
actual_direction
scored_flag
LIT101
FIT101
robust_change_5s_percent
expected_change_5s_percent
lower_normal
upper_normal
lower_critical
upper_critical
dynamics_residual
base_risk
model_risk
model_level
model_reason
```

For `NOT_SCORED` rows:

```text
exceedance = NaN
base_risk = NaN
expected_direction = NaN
actual_direction = NaN
model_risk = 0.0
model_level = NOT_SCORED
```

The group envelope file should also store the calibration constants used for traceability:

```text
state_key
flow_bin
support_count
flow_epsilon
flow_p50_threshold
deadband
lower_tail_width
upper_tail_width
median_change
P1
P5
P95
P99
```

## 20. Metrics

Generate:

```text
processed/b2_dynamics_metrics.csv
processed/b2_dynamics_group_envelopes.csv
processed/b2_dynamics_top_examples.csv
processed/b2_dynamics_case_studies.csv
```

Metrics should include:

```text
normal validation rows with model_risk >= 0.5
normal validation command rows with model_risk >= 0.5
attack rows with model_risk >= 0.5
attack command rows with model_risk >= 0.5
mean model_risk normal vs attack
median model_risk normal vs attack
phase-wise false alert rate
phase-wise attack detection rate
number of NOT_SCORED rows
number of low-support groups
```

Because only a small number of SWaT attacks directly touch Stage 1, named case studies should be treated as the primary B2 evidence. The case-study file should include:

```text
attack timestamp or attack window
state_key
flow_bin
robust_change_5s_percent
expected_change_5s_percent
dynamics_residual
model_risk
model_reason
whether B1 allowed or flagged the same row, if available
```

The aggregate phase-wise attack table is supporting evidence, not the headline result.

## 21. Plots

Generate:

```text
plots/b2_dynamics/b2_model_risk_distribution.png
plots/b2_dynamics/b2_phase_risk_summary.png
plots/b2_dynamics/b2_level_change_envelopes.png
plots/b2_dynamics/b2_top_abnormal_examples.png
```

## 22. Report-Ready Explanation

B2 is a state-aware residual dynamics model. It learns normal 5-second tank-level movement from normal SWaT Stage 1 data for each actuator-state and flow condition. Instead of only checking whether the tank is near overflow or underflow, B2 checks whether the observed tank movement is consistent with normal operation in that state. It uses robust level-change estimation, normal-only calibration, validation-based deadband selection, calibrated flow bins, tail-width safeguards, and calibrated envelopes to convert movement residuals into a model risk score. Rows near actuator/phase boundaries or with insufficient normal support are marked as `NOT_SCORED` and treated neutrally during fusion.

## 23. Why B2 Is Useful

B1 catches physically unsafe situations, such as projected overflow or underflow.

B2 catches abnormal process behavior before hard safety limits are reached.

Example:

```text
Tank level = 55%
Valve = Open
Pumps = OFF
Expected behavior = tank should rise
Actual behavior = tank barely rises or falls
```

B1 may allow this because the tank is still far from overflow/underflow.

B2 can flag it because the tank behavior does not match the learned normal dynamics for that state.

## 24. Final Locked Definition

> B2 is a state-aware residual dynamics model with actuator-state and calibrated flow-bin conditioning, robust 5-second movement, normal-only calibration, validation-based deadband, tail-width floor, stable-expected group safeguard, and neutral `NOT_SCORED` handling.
