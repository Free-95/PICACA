# B3 Final Specification: Safety-Preserving Hybrid Fusion Model

## 1. Purpose

B3 is the fixed-lookahead hybrid fusion layer of PICACA-Lite.

Its purpose is to combine:

```text
B1 physics risk
B1 sensor risk
B2 dynamics risk
Yash V3 weighted context risk
```

B3 must preserve B1 as the minimum safety baseline:

```text
B3 severity >= B1 severity
```

Fusion can raise caution, but it must never reduce a B1 `DELAY_ALERT` or `BLOCK_ALARM`.

## 2. Inputs

B3 reads:

```text
processed/swat_b1_final_results.csv
processed/swat_b2_dynamics_results.csv
processed/swat_stage1_context_risk_v3.csv
```

Normalize timestamp keys before merging:

```text
b1.timestamp -> Timestamp
b2.Timestamp -> Timestamp
v3.Timestamp -> Timestamp
```

Use an inner join on:

```text
Timestamp
```

Assert that all three files have identical timestamp coverage. If any timestamp is missing from B1, B2, or V3, B3 should fail loudly rather than silently drop or duplicate rows.

If a V3 context row is missing, use:

```text
context_risk_weighted = 0.0
context_reason = "MISSING_CONTEXT_TREATED_AS_NEUTRAL"
```

Do not forward-fill missing context values.

Required B1 fields:

```text
Timestamp
label
command_type
physics_risk
sensor_risk
decision
```

Required B2 fields:

```text
Timestamp
event_type
model_risk
scored_flag
model_level
model_reason
```

Required V3 fields:

```text
Timestamp
event_type
context_risk_weighted
context_risk_max
context_reason
context_level_weighted
```

Use:

```text
context_risk = context_risk_weighted
```

`context_risk_max` is retained only for debugging and comparison.

`context_level_weighted` is reporting-only for B3; the decision logic uses the numeric `context_risk_weighted`.

## 3. Shared Definitions

Command row:

```text
command_type != "NO_COMMAND"
```

B3 must assert that the command-row definitions are identical across the merged files:

```text
B1 command row: command_type != "NO_COMMAND"
B2/V3 command row: event_type != "NONE"
```

If these do not match timestamp-for-timestamp, B3 should fail loudly because the comparison tables would no longer be using the same command-row set.

Severity:

```text
ALLOW = 0
DELAY_ALERT = 1
BLOCK_ALARM = 2
```

Severity merge:

```text
max_severity(a, b)
```

returns the more cautious decision.

## 4. Scale Harmonization

All four risks must be bounded in `[0, 1]`.

| Risk | Meaning |
|---|---|
| `physics_risk` | Projected overflow/underflow risk from B1 |
| `sensor_risk` | Sensor inconsistency/spike risk from B1 |
| `b2_effective_risk` | Recent/current dynamics inconsistency from B2 |
| `context_risk_weighted` | Command-context suspicion from Yash V3 |

B1 verification showed:

```text
On command rows, physics_risk >= 0.85 always maps to B1 BLOCK_ALARM.
On NO_COMMAND rows, physics_risk >= 0.85 can still be B1 ALLOW because no actuator command is being authorized.
```

Therefore B3 must not use `physics_risk >= 0.85` as an unconditional all-row BLOCK gate.

## 5. B2 Effective Risk

Use current B2 risk when the current row is scored:

```text
if scored_flag == True:
    b2_effective_risk = model_risk
```

If the current row is not scored:

```text
look back over the 30 seconds immediately before the current timestamp
```

Use B2 rows in that lookback window that satisfy:

```text
scored_flag == True
model_risk >= 0.5
```

If at least two elevated rows exist:

```text
b2_effective_risk = second_highest(model_risk in elevated recent rows)
```

If fewer than two elevated rows exist:

```text
b2_effective_risk = 0.0
```

This carries sustained dynamics evidence into command/boundary rows without letting one isolated spike dominate.

## 6. B3 Score

Default full B3 weights:

```text
physics = 0.35
sensor = 0.20
B2 dynamics = 0.25
context = 0.20
```

Full B3 score:

```text
b3_score =
0.35 * physics_risk
+ 0.20 * sensor_risk
+ 0.25 * b2_effective_risk
+ 0.20 * context_risk_weighted
```

This score is a graded suspicion score. It cannot lower the B1 decision.

## 7. Decision Logic

Use this branch order:

```text
if B1 decision == BLOCK_ALARM:
    B3 decision = BLOCK_ALARM

elif command row and (physics_risk >= 0.85 or sensor_risk >= 0.85):
    B3 decision = BLOCK_ALARM

elif physics_risk >= 0.85 or sensor_risk >= 0.85:
    B3 decision = max_severity(B1 decision, DELAY_ALERT)

elif command row and b3_score >= block_threshold:
    B3 decision = BLOCK_ALARM

elif b3_score >= block_threshold:
    B3 decision = max_severity(B1 decision, DELAY_ALERT)

elif b3_score >= delay_threshold:
    B3 decision = max_severity(B1 decision, DELAY_ALERT)

elif context_risk_weighted >= 0.5 and b2_effective_risk >= 0.5:
    B3 decision = max_severity(B1 decision, DELAY_ALERT)

else:
    B3 decision = B1 decision
```

Starting thresholds:

```text
delay_threshold = 0.55
block_threshold = 0.85
```

These must be validated on normal-validation rows before freezing.

## 8. Ablation Rules

Ablations must use the same safety-preserving decision logic.

For fair score comparison, renormalize weights over the components present in each ablation.

All ablations reuse the frozen full-B3 `delay_threshold` and `block_threshold`.

### B1 Alone

```text
Use B1 decision only.
```

### B1 + B2

Use:

```text
physics_risk
sensor_risk
b2_effective_risk
context_risk_weighted = absent
```

Renormalized weights:

```text
physics = 0.35 / 0.80 = 0.4375
sensor = 0.20 / 0.80 = 0.2500
B2 dynamics = 0.25 / 0.80 = 0.3125
```

### B1 + Context

Use:

```text
physics_risk
sensor_risk
context_risk_weighted
b2_effective_risk = absent
```

Renormalized weights:

```text
physics = 0.35 / 0.75 = 0.4667
sensor = 0.20 / 0.75 = 0.2667
context = 0.20 / 0.75 = 0.2667
```

### Full B3

Use:

```text
physics_risk
sensor_risk
b2_effective_risk
context_risk_weighted
```

Weights:

```text
physics = 0.35
sensor = 0.20
B2 dynamics = 0.25
context = 0.20
```

## 9. Threshold Validation

Use only normal-validation rows to choose final B3 thresholds.

Candidate thresholds:

```text
delay_threshold: 0.45, 0.50, 0.55, 0.60, 0.65
block_threshold: 0.80, 0.85, 0.90
```

After selecting thresholds using normal validation only, freeze them and report the final normal + attack results at that operating point.

Attack rows must not be used to choose thresholds.

## 10. Weight Sensitivity Sweep

Do not train weights on attack rows.

Use fixed rationale:

```text
physics = 0.35 because physical safety is primary
sensor = 0.20 because sensor trust is important but separate
B2 dynamics = 0.25 because learned process behavior provides independent evidence
context = 0.20 because Yash context is supporting command-side suspicion
```

Then run a sensitivity sweep:

```text
change each weight by +/- 0.10
renormalize weights to sum to 1
compare decision counts
```

Report whether B3 decisions are stable.

## 11. Outputs

Generate:

```text
processed/swat_b3_fusion_results.csv
processed/b3_metrics_summary.csv
processed/b3_ablation_summary.csv
processed/b3_weight_sensitivity.csv
processed/b3_threshold_sweep.csv
processed/b3_case_studies.csv
```

Plots:

```text
plots/b3_fusion/b3_decision_distribution.png
plots/b3_fusion/b3_ablation_comparison.png
plots/b3_fusion/b3_score_distribution.png
plots/b3_fusion/b3_weight_sensitivity.png
```

## 12. Report-Ready Explanation

B3 is a safety-preserving hybrid fusion model. It combines B1 physics risk, B1 sensor risk, B2 effective dynamics risk, and Yash's weighted context risk. Unlike a plain weighted average, B3 preserves B1 as the minimum safety baseline, so fusion can only increase caution and never downgrade a physical safety decision. B3 still uses the fixed B1 lookahead; adaptive lookahead is reserved for B4.

## 13. Final Locked Definition

> B3 is a fixed-lookahead, safety-preserving fusion model that combines physics risk, sensor risk, learned dynamics risk, and weighted command-context risk while guaranteeing that the final B3 decision is never less cautious than B1.
