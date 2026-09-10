# Modbus Register Map — HIL Single-Tank Rig (to be filled by the colleague)

The PICACA-Lite engines consume a fixed set of Stage-1 signals at **1 Hz**.
Map each to the rig's actual Modbus register before wiring up the port.

| Engine signal | Meaning | Unit expected by code | Modbus addr | Register type | Scaling / word order | Notes |
|---|---|---|---|---|---|---|
| `LIT101` | Tank level | mm (0–1000 full scale) | | (holding / input) | | B1 % = `LIT101 / 1000 * 100` |
| `FIT101` | Inlet flow | m³/h | | | | Used for B2 `flow_bin` only |
| `MV101_state` | Inlet valve state | `Open` / `Closed` / `Transition` | | (coil / discrete / holding) | | If the rig gives a raw int, map it the way `swat_cleaner.py` does |
| `P101_state` | Transfer pump 1 | 0 / 1 | | (coil / discrete) | | |
| `P102_state` | Transfer pump 2 (backup) | 0 / 1 | | (coil / discrete) | | |

## Poll / timing

| Item | Value |
|---|---|
| Poll interval | **must present 1 sample/second to the engines** |
| If rig polls faster | decimate to 1 Hz, OR rescale the sample-count constants: `b2_config: smoothing_window_samples (9), change_horizon_samples (5)`; feature windows `300` / `60`; `b3_config: b2_lookback_seconds (30)` |
| Timestamp source | monotonic seconds; used for V3 `time_since_last_command` / `time_since_same_command` |

## Actuator command capture

`event_type` is derived on-device by edge detection, not read from a register:

| Transition observed this tick | `event_type` emitted |
|---|---|
| `P101_state` 0 → 1 | `P101_ON` |
| `P101_state` 1 → 0 | `P101_OFF` |
| `P102_state` 0 → 1 | `P102_ON` |
| `P102_state` 1 → 0 | `P102_OFF` |
| `MV101_state` changed (any) | `MV101_CHANGE` |
| more than one of the above in one tick | join with `;` (still counts as ONE command row) |
| none | `NONE` |

If the demo issues commands through a separate SCADA/Modbus write path, you may capture
the *intent* directly instead of inferring it from state edges — but keep the
`event_type` string values identical, because `v3_calibration_full.json` is keyed on them.
