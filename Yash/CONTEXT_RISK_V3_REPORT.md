# Context Risk V3 Model Report — SWaT Stage 1

## Executive Summary
Context Risk V3 evaluates pure command-context anomalies without relying on physical state predictions or process alarm thresholds.

## Model Configuration & Weights
- **Calibration Fraction**: 80% Normal rows
- **Laplace Smoothing Addend (k)**: 1
- **Minimum Transition Support**: 3
- **Component Weights**:
  - Command Frequency: `0.20`
  - Command Repetition: `0.20`
  - Command Timing: `0.20`
  - Command Sequence (Markov): `0.25`
  - Phase Rarity: `0.15`

## Calibration Statistics
- **Calibration Rows**: 316,238 Normal rows
- **Validation Rows**: 79,060 Normal rows
- **Attack Test Rows**: 54,621 Attack rows
- **Command Types**: 4
- **Process Phases**: 5

## Sparse / Unseen Transition Audit
- **Total Normal Command Rows**: 701
- **Sparse Transitions (<3 support)**: 10 (1.43%)
- **High-Risk Sparse Rows**: 5 (50.0% of sparse)

## Performance Summary
- **Normal Validation Mean Risk (Max / Weighted)**: `0.0652` / `0.0133`
- **Attack Test Mean Risk (Max / Weighted)**: `0.0122` / `0.0026`

## Output Files Generated
- Main CSV: `swat_stage1_context_risk_v3.csv`
- Metrics: `context_risk_v3_metrics.csv`
- Command Metrics: `context_risk_v3_command_metrics.csv`
- Threshold Curve: `context_risk_v3_threshold_curve.csv`
- Top Examples: `context_risk_v3_top_examples.csv`
- Artifacts: `E:\ISA_PROJECT\context_engine\v3_artifacts\v3_calibration.json`
- Plots Directory: `E:\ISA_PROJECT\plots\context_risk_v3`
