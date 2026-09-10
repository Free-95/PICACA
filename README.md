# PICACA-Lite B3 — ESP32 Port Package

Prepared for the HIL / Modbus single-tank demo.
Contents assembled 2026-09-09 from the PICACA-Lite working tree.

---

## 1. Read this first

**B3 is not a model file.** `swat_b3_fusion_engine.py` is a batch Python pipeline that
*merges three already-computed CSVs* (`swat_b1_final_results.csv`,
`swat_b2_dynamics_results.csv`, `swat_stage1_context_risk_v3.csv`) and applies the
fusion decision logic row by row. Nothing in it is a trained network.

To run "B3" on an ESP32 you must **re-implement four things in C/C++**, live, from the
tank's Modbus signals:

```
Modbus registers (level, flow, MV101/P101/P102 states)
        │
        ├─► feature layer   : build event_type, process_phase, robust slope,
        │                      rolling command counts   (swat_event_extractor.py +
        │                      swat_context_features.py)
        │
        ├─► B1  physics      : swat_b1_physics_engine.py  → physics_risk, sensor_risk,
        │                      projected_level, B1 decision
        │
        ├─► B2  dynamics     : swat_b2_dynamics_model.py  (score_rows only) → model_risk
        │                      using the frozen envelope table
        │
        ├─► V3  context      : context_risk_v3.py (calc_risk_* + weighted sum) →
        │                      context_risk_weighted   using the frozen calibration
        │
        └─► B3  fusion       : swat_b3_fusion_engine.py (decide_fusion) →
                               ALLOW / DELAY_ALERT / BLOCK_ALARM  + reason
```

The arithmetic is light (a few multiplies, comparisons, one small lookup table per
stage). The only things that need memory are a handful of ring buffers (Section 6).

**Scope decision already made:** the live context layer (V3) IS included in this port.
`v3_calibration_full.json` in `03_frozen_parameters/` carries everything V3 needs.

---

## 2. Folder contents

### `01_specs/` — the authoritative logic, in prose

| File | What it is |
|---|---|
| `b3_final_spec.md` | The B3 fusion spec: score formula, weights, thresholds, the full decision branch order, the safety-preservation rule (`B3 severity ≥ B1 severity`). **This is the contract for the fusion layer.** |
| `b2_final_spec.md` | B2 runtime spec: robust 5-second smoothing, `state_key` construction, flow binning, the exceedance formula, wrong-direction penalty, stable-group guard, `NOT_SCORED` rules, risk→level mapping. |
| `b1_final_method.md` | B1 physics baseline method: level zones, fixed 5 s lookahead, projected-level logic, sensor-consistency checks. |
| `b1_final_report_section.md` | Longer prose writeup of B1 (context, worked examples). |
| `context_risk_v2_README.md` | Background on the context-risk approach. V3 is the version in use; treat V2 notes as history. The V3 logic lives in the code + `v3_calibration_full.json`. |

### `02_reference_code/` — the Python ground truth to match

| File | Role in the port |
|---|---|
| `swat_b3_fusion_engine.py` | Port `decide_fusion()` (lines ~179-208) and `B3Config` (lines ~27-38). `b2_effective_risk` logic is `add_b2_effective_risk()` + `second_highest()`. Ignore the plotting / metrics / ablation halves. |
| `swat_b2_dynamics_model.py` | Port `score_rows()` (the per-row block), `assign_flow_bin()`, `direction()`, `is_wrong_direction()`, `risk_level()`, and `add_state_and_motion_features()` (smoothing + `state_key` + boundary window). `B2Config` = lines ~30-39. **Do NOT port the `fit_*` functions** — that calibration is already done and frozen in `b2_dynamics_group_envelopes.csv`. |
| `swat_b1_physics_engine.py` | Port `decide_b1()` and every helper it calls (`classify_level`, `classify_trend`, `infer_command_effect`, `projected_trend`, `overflow_risk`, `underflow_risk`, `sensor_consistency_risk`). `B1Config` = lines ~50-68 holds **all** B1 constants. Fully self-contained, no external table. |
| `context_risk_v3.py` | Port `calc_risk_cmd_frequency`, `calc_risk_cmd_repetition`, `calc_risk_cmd_timing`, `calc_risk_command_sequence`, `calc_risk_phase_rarity`, and `calculate_candidate_scores()` (the weighted sum → `context_risk_weighted`). **Do NOT port the `fit_*` functions** — frozen in `v3_calibration_full.json`. `_sigmoid_risk()` is a plain linear ramp. |
| `swat_event_extractor.py` | Defines how `event_type` / `event_value` are derived from raw actuator states: P101/P102 rising edge → `P101_ON`/`P102_ON`, falling edge → `_OFF`; any MV101 state change → `MV101_CHANGE`. Multiple in one tick are joined with `;` (compound event = one command row). Also computes `level_change`, `level_slope`. |
| `swat_context_features.py` | Defines `process_phase` (`determine_phase()`), the rolling `command_frequency` (300 s) / `command_frequency_60s` counts, `command_repetition`, `consecutive_same_command`, and `robust_level_slope` (300 s median of `level_change`). The ESP32 feature layer must reproduce these. |
| `swat_cleaner.py` | Raw-CSV cleaning (column rename, MV101 numeric→`Open`/`Closed`/`Transition`, dedup). Only relevant for how the raw SWaT columns map to the names everything else uses. |

### `03_frozen_parameters/` — the numbers (ship these as C headers / arrays)

| File | Contents |
|---|---|
| `b1_config_defaults.json` | All 16 B1 constants (level zone edges, `fixed_lookahead_seconds=5`, nominal fill/drain rates, trend thresholds, `sensor_spike_mm_5s=50`, `contradictory_trend_pct_s=0.0887`, `active_flow_threshold=1.5`, delay/block risk thresholds). |
| `b2_config_defaults.json` | B2 runtime constants: `change_horizon_samples=5`, `smoothing_window_samples=9`, `min_support=100`, `tail_width_floor=0.02`, `risk_threshold=0.5`, `stable_envelope_limit=0.05`. |
| `b3_config_defaults.json` | B3: weights `physics 0.35 / sensor 0.20 / b2 0.25 / context 0.20`, `delay_threshold 0.55`, `block_threshold 0.85`, `hard_gate_threshold 0.85`, `agreement_threshold 0.50`, `b2_lookback_seconds 30`. |
| `b2_dynamics_group_envelopes.csv` | **The B2 frozen lookup table.** One row per `state_key × flow_bin`: `median_change`, `P1/P5/P95/P99`, `lower_tail_width`, `upper_tail_width`, `expected_direction`, `stable_envelope_tight`, `support_count`. The constants `flow_epsilon` and `flow_p50_threshold` and `deadband` are identical in every row — read them once. Groups with `support_count < 100` → B2 returns `NOT_SCORED` for that group. ~10 rows total. |
| `v3_calibration_full.json` | **The complete frozen V3 calibration** (auto-exported for this package because the pipeline only saved a partial JSON). Contains: the 5 component `weights` (freq 0.20 / rep 0.20 / timing 0.20 / sequence 0.25 / phase 0.15), the `levels` cutoffs, `freq_model` / `rep_model` percentile thresholds, `timing_model` lognormal params, and the full `markov_prev` (4×4), `markov_prev_phase` (4×5×4), `markov_phase` (5×4), `phase_rarity_model` (5×4) probability tables plus `markov_support_prev`. Vocabulary: **4 command types** (`MV101_CHANGE`, `P101_OFF`, `P101_ON`, `P102_ON`), **5 phases** (`DRAINING`, `FILLING`, `HOLDING`, `TRANSFERRING`, `TRANSITIONING`). Total ≈ 140 floats — trivial in flash. |
| `v3_calibration_partial.json` | The JSON the pipeline actually wrote (no Markov tables). Kept only so you can diff against Yash's original. **Use `v3_calibration_full.json`.** |

### `04_validation_vectors/` — check the C port against these

| File | Use |
|---|---|
| `b3_reference_io_slice.csv` | 400 rows from the full Python B3 run: every command row, every non-ALLOW row, every row with a non-zero B2 risk, plus 15 plain monitoring rows. Columns include all four input risks, `b3_score`, B1 `decision`, and `b3_decision`. Feed the input columns into your C port and confirm `b3_score` (±1e-4) and `b3_decision` match exactly. |
| `swat_stage1_b1_sample20.csv` | 20-row sample in the B1 input schema — good for a first B1-only unit test. |

### `05_schema_and_pipeline/`

| File | Use |
|---|---|
| `swat_stage1_b1_schema.md` | Column definitions for the B1 input row. |
| `B1_HANDOFF_README.md` | Yash→Divija handoff notes: B1 input contract, the mm→percent trend convention. |
| `RUN_ORDER_AND_OUTPUTS.md` | The full offline pipeline order (`cleaner → event_extractor → context_features → b1 / b2 / v3 → b3`) and every output file. Reference only — the ESP32 collapses all of this into one per-tick function. |

---

## 3. Porting order (recommended)

1. **Feature layer.** From the Modbus poll, build per tick: `LIT101` (mm), `FIT101`
   (m³/h), `MV101_state` ∈ {`Open`,`Closed`,`Transition`}, `P101_state`, `P102_state`
   ∈ {0,1}. Derive `event_type` (edge detection, `swat_event_extractor.py`),
   `process_phase` (`determine_phase()`), `level_change` (= LIT101 tick-to-tick diff),
   `robust_level_slope` (300-sample median of `level_change`), rolling
   `command_frequency` (300 s) and `command_frequency_60s`, `command_repetition`,
   `consecutive_same_command`.
2. **B1.** Port `decide_b1()`. Test against `swat_stage1_b1_sample20.csv`.
   Note the trend unit: B1 expects `trend_pct_s`; Yash's raw trend is
   `LIT101(t) − LIT101(t−5)` in **mm**, and `convert_yash_trend_to_pct_s()` divides
   by **50** (10 mm = 1 % of a 1000 mm tank, over 5 s).
3. **B2 scoring.** Port `score_rows()`. Load `b2_dynamics_group_envelopes.csv` into a
   small array. Build `state_key = "<MV101_state>_<P101_state>_<P102_state>"`,
   `flow_bin` from `assign_flow_bin(FIT101, flow_epsilon, flow_p50)`. Maintain a
   9-sample LIT101 ring for the rolling median; the 5-sample-ago smoothed value gives
   `robust_change_5s_percent`. Apply boundary-window exclusion
   (`max(5,9)=9` samples after any command or phase change → `NOT_SCORED`).
4. **V3 context.** Port the 5 `calc_risk_*` funcs + weighted sum. Load
   `v3_calibration_full.json`. Keep `last_cmd` across command rows for the Markov term,
   and per-command-type last-timestamp for `time_since_same_command`.
5. **B3 fusion.** Port `decide_fusion()`. Compute `b2_effective_risk`
   (current `model_risk` if the row is B2-scored, else the second-highest B2
   `model_risk ≥ 0.5` seen in the previous 30 samples, else 0.0). Compute `b3_score`
   with the four weights, then walk the branch order. Assert the output severity is
   never below the B1 decision severity.
6. **Validate** the whole chain against `b3_reference_io_slice.csv`.

---

## 4. Runtime state the ESP32 must hold

| Buffer | Size | For |
|---|---|---|
| LIT101 ring | ≥ 14 samples (9 smoothing + 5 horizon) | B2 robust 5 s change; B1 trend |
| `level_change` ring | 300 samples | `robust_level_slope` (300 s median) |
| `is_event` ring | 300 samples (+ 60) | `command_frequency`, `command_frequency_60s` |
| B2 `model_risk` ring | 30 samples | `b2_effective_risk` 30 s bridge |
| `last_cmd` | 1 value | V3 Markov sequence term |
| last-timestamp per command type | 4 values | V3 `time_since_same_command` |
| last-any-command timestamp | 1 value | V3 `time_since_last_command` |
| `consecutive_same_command` counter | 1 value | V3 repetition term |

At 1 Hz these are a few kB total.

---

## 5. Known gaps / decisions for the colleague

1. **Modbus register map is not in this package.** Fill
   `MODBUS_REGISTER_MAP_TEMPLATE.md` with the tank rig's actual register numbers,
   data types, scaling, and poll rate. The engines assume a **1 Hz** sample; if the
   rig polls faster, decimate to 1 Hz or re-derive the sample-count constants
   (`smoothing_window_samples`, `change_horizon_samples`, the 300/60/30 windows).
2. **`v3_calibration_full.json` was auto-generated for this package** by re-running
   Yash's `fit_*` functions on `swat_stage1_context_features.csv`. It should match his
   original calibration exactly; if in doubt, have Yash regenerate and diff.
3. **The context vocabulary is fixed at 4 command types.** If the HIL rig can emit a
   command the SWaT data never contained, V3 has no calibrated entry for it — decide
   whether to treat unknown commands as max sequence/phase risk or as neutral.
4. **B1 `sensor_risk` uses a raw 5 s mm jump threshold (`sensor_spike_mm_5s = 50`).**
   On real hardware, sensor noise characteristics differ from the SWaT historian —
   re-check this threshold against a few minutes of quiet rig data before trusting the
   sensor-spoof gate.
5. **Determinism check:** the Python B3 asserts monotonicity (`B3 ≥ B1`). Keep that
   assertion in the C port — it is the core safety property of the demo.

---

## 6. One-line summary for the handoff email

> B3 is deterministic fusion logic, not a trained model. This folder has the four
> engine sources to port (B1 physics, B2 dynamics-scoring, V3 context, B3 fusion),
> their frozen parameter tables (`b2_dynamics_group_envelopes.csv`,
> `v3_calibration_full.json`, three `*_config_defaults.json`), and a 400-row
> reference I/O slice to validate the C implementation against. Fill in the Modbus
> register map for the rig, keep the 1 Hz assumption, and preserve the
> "B3 never downgrades B1" check.
