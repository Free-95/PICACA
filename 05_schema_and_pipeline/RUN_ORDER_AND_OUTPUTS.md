# PICACA / SWaT Local Pipeline Run Order

Working folder:

```text
C:\Users\Divija\Desktop\Sem-7\ISA
```

Raw files already present:

```text
normal.csv
attack.csv
merged.csv
```

Install dependencies once:

```bash
pip install pandas numpy matplotlib
```

## Main Run Order

Run these from VS Code terminal opened in `C:\Users\Divija\Desktop\Sem-7\ISA`.

### 1. Clean Stage 1 Raw Data

```bash
python swat_cleaner.py
```

Inputs:

```text
normal.csv
attack.csv
merged.csv
```

Outputs:

```text
processed/swat_stage1_normal.csv
processed/swat_stage1_attack.csv
processed/swat_stage1_clean.csv
```

### 2. Extract Actuator Events And Physical Slopes

```bash
python swat_event_extractor.py
```

Input:

```text
processed/swat_stage1_clean.csv
```

Outputs:

```text
processed/swat_stage1_events.csv
plots/stage1_events_overview.png
```

### 3. Generate Command Context Features

```bash
python swat_context_features.py
```

Input:

```text
processed/swat_stage1_events.csv
```

Outputs:

```text
processed/swat_stage1_context_features.csv
plots/context_features/command_frequency_over_time.png
plots/context_features/command_repetition_over_time.png
plots/context_features/command_value_changes.png
plots/context_features/level_and_trends.png
plots/context_features/process_phase_distribution.png
plots/context_features/normal_vs_attack_feature_comparison.png
```

### 4. Calibrate Normal Baseline

```bash
python calibrate_baseline.py
```

Input:

```text
processed/swat_stage1_context_features.csv
```

Output:

```text
context_engine/baseline/normal_baseline_stats.json
```

### 5. Generate Context Risk v1

```bash
python context_engine/context_risk.py
```

Inputs:

```text
processed/swat_stage1_context_features.csv
context_engine/baseline/normal_baseline_stats.json
```

Outputs:

```text
processed/swat_stage1_context_risk.csv
plots/context_risk/context_risk_over_time.png
plots/context_risk/normal_vs_attack_context_risk.png
plots/context_risk/context_risk_components.png
plots/context_risk/low_and_slow_examples.png
```

### 6. Generate Context Risk v2

```bash
python context_engine/context_risk_v2.py
```

Inputs:

```text
processed/swat_stage1_context_features.csv
processed/swat_stage1_context_risk.csv
context_engine/baseline/normal_baseline_stats.json
```

Outputs:

```text
processed/swat_stage1_context_risk_v2.csv
plots/context_risk_v2/old_vs_new_context_risk.png
plots/context_risk_v2/normal_vs_attack_v2.png
plots/context_risk_v2/risk_components_v2.png
plots/context_risk_v2/normal_false_positive_analysis.png
plots/context_risk_v2/low_and_slow_v2.png
```

### 7. Create B1 Physical Handoff Dataset

```bash
python create_and_validate_b1_handoff.py
```

Inputs:

```text
processed/swat_stage1_clean.csv
processed/swat_stage1_events.csv
```

Outputs:

```text
processed/swat_stage1_b1_input.csv
processed/swat_stage1_b1_sample20.csv
```

### 8. Run Divija's B1 Physics Engine

```bash
python swat_b1_physics_engine.py
```

Default input:

```text
processed/swat_stage1_b1_input.csv
```

Default output:

```text
processed/swat_b1_full_results.csv
```

Equivalent explicit command:

```bash
python swat_b1_physics_engine.py --input processed/swat_stage1_b1_input.csv --output processed/swat_b1_full_results.csv
```

## Optional Inspection Scripts

Run only after the required input file exists.

```bash
python inspect_events.py
python inspect_command_intervals.py
python inspect_large_changes.py
python inspect_sim.py
python inspect_attack_patterns.py
python audit_context_risk.py
python test_v2_risk.py
python test_v2_refined.py
```

## Important Note

The SWaT normal and attack CSVs overlap in real timestamp range. If a validation printout mentions duplicate timestamps in combined outputs, it is due to the original dataset clocks overlapping. For our B1 work, the key required output is:

```text
processed/swat_stage1_b1_input.csv
```

and the B1 result file is:

```text
processed/swat_b1_full_results.csv
```
