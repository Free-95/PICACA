"""
PICACA-Lite Student 2:
B2 state-aware residual dynamics model for SWaT Stage 1.

This module learns normal 5-second tank movement for each actuator-state and
flow condition, then scores whether observed movement is consistent with that
learned normal dynamics. It does not make ALLOW / DELAY / BLOCK decisions.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


LEVEL_COLUMNS = [
    "LOW",
    "MEDIUM",
    "HIGH",
    "VERY_HIGH",
    "NOT_SCORED",
]


@dataclass(frozen=True)
class B2Config:
    change_horizon_samples: int = 5
    smoothing_window_samples: int = 9
    min_support: int = 100
    preferred_support: int = 300
    tail_width_floor: float = 0.02
    risk_threshold: float = 0.5
    stable_envelope_limit: float = 0.05


def clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def risk_level(risk: float, scored: bool) -> str:
    if not scored:
        return "NOT_SCORED"
    if risk < 0.30:
        return "LOW"
    if risk < 0.60:
        return "MEDIUM"
    if risk < 0.85:
        return "HIGH"
    return "VERY_HIGH"


def direction(value: float, deadband: float) -> str:
    if pd.isna(value):
        return "UNKNOWN"
    if abs(value) <= deadband:
        return "STABLE"
    if value > 0:
        return "RISING"
    return "FALLING"


def is_wrong_direction(expected_direction: str, actual_direction: str) -> bool:
    return (
        expected_direction == "RISING"
        and actual_direction == "FALLING"
    ) or (
        expected_direction == "FALLING"
        and actual_direction == "RISING"
    )


def load_input(input_path: Path) -> pd.DataFrame:
    df = pd.read_csv(input_path)
    required = {
        "Timestamp",
        "LIT101",
        "FIT101",
        "MV101_state",
        "P101_state",
        "P102_state",
        "process_phase",
        "event_type",
        "Normal/Attack",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    df = df.sort_values("Timestamp").reset_index(drop=True)
    return df


def add_state_and_motion_features(df: pd.DataFrame, config: B2Config) -> pd.DataFrame:
    df = df.copy()
    df["state_key"] = (
        df["MV101_state"].astype(str)
        + "_"
        + df["P101_state"].astype(int).astype(str)
        + "_"
        + df["P102_state"].astype(int).astype(str)
    )
    df["is_command_row"] = df["event_type"].astype(str).ne("NONE")

    df["smooth_LIT101"] = (
        df["LIT101"]
        .rolling(window=config.smoothing_window_samples, min_periods=1)
        .median()
    )
    df["robust_change_5s_percent"] = (
        (df["smooth_LIT101"] - df["smooth_LIT101"].shift(config.change_horizon_samples))
        / 1000.0
        * 100.0
    )

    phase_changed = df["process_phase"].ne(df["process_phase"].shift(1))
    boundary_start = df["is_command_row"] | phase_changed
    boundary_window = max(config.change_horizon_samples, config.smoothing_window_samples)
    df["boundary_excluded"] = (
        boundary_start.astype(int)
        .rolling(window=boundary_window, min_periods=1)
        .max()
        .astype(bool)
    )
    return df


def normal_calibration_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    normal = df[df["Normal/Attack"] == "Normal"].copy()
    split_index = int(len(normal) * 0.80)
    calibration = normal.iloc[:split_index].copy()
    validation = normal.iloc[split_index:].copy()
    attack = df[df["Normal/Attack"] == "Attack"].copy()
    return calibration, validation, attack


def fit_flow_bins(df: pd.DataFrame) -> tuple[float, float]:
    no_inlet = df[df["process_phase"].isin(["HOLDING", "DRAINING"])]["FIT101"].dropna()
    if no_inlet.empty:
        no_inlet = df["FIT101"].dropna()
    flow_epsilon = float(np.percentile(no_inlet, 95)) if len(no_inlet) else 0.0

    flowing = df[df["FIT101"] > flow_epsilon]["FIT101"].dropna()
    if flowing.empty:
        flowing = df[df["FIT101"] > 0]["FIT101"].dropna()
    flow_p50 = float(np.percentile(flowing, 50)) if len(flowing) else flow_epsilon
    return flow_epsilon, flow_p50


def assign_flow_bin(fit101: float, flow_epsilon: float, flow_p50: float) -> str:
    if fit101 <= flow_epsilon:
        return "LOW_FLOW"
    if fit101 <= flow_p50:
        return "MEDIUM_FLOW"
    return "HIGH_FLOW"


def fit_deadband(calibration: pd.DataFrame) -> float:
    stable = calibration[
        (calibration["state_key"] == "Closed_0_0")
        & (calibration["process_phase"] == "HOLDING")
        & (~calibration["boundary_excluded"])
    ]["robust_change_5s_percent"].dropna()
    if stable.empty:
        stable = calibration[
            (calibration["process_phase"] == "HOLDING")
            & (~calibration["boundary_excluded"])
        ]["robust_change_5s_percent"].dropna()
    if stable.empty:
        stable = calibration["robust_change_5s_percent"].dropna()
    return float(np.percentile(stable.abs(), 95)) if len(stable) else 0.01


def fit_envelopes(
    calibration: pd.DataFrame,
    flow_epsilon: float,
    flow_p50: float,
    deadband: float,
    config: B2Config,
) -> pd.DataFrame:
    usable = calibration[
        (~calibration["boundary_excluded"])
        & calibration["robust_change_5s_percent"].notna()
    ].copy()
    usable["flow_bin"] = usable["FIT101"].apply(
        lambda value: assign_flow_bin(value, flow_epsilon, flow_p50)
    )

    rows: list[dict[str, object]] = []
    grouped = usable.groupby(["state_key", "flow_bin"], dropna=False)
    for (state_key, flow_bin), group in grouped:
        values = group["robust_change_5s_percent"].dropna()
        support = int(len(values))
        if support == 0:
            continue
        p1, p5, median, p95, p99 = np.percentile(values, [1, 5, 50, 95, 99])
        lower_tail_width = max(float(p5 - p1), config.tail_width_floor)
        upper_tail_width = max(float(p99 - p95), config.tail_width_floor)
        expected_direction = direction(float(median), deadband)
        stable_envelope_tight = bool(
            expected_direction != "STABLE"
            or (abs(float(p5)) < config.stable_envelope_limit and abs(float(p95)) < config.stable_envelope_limit)
        )
        rows.append(
            {
                "state_key": state_key,
                "flow_bin": flow_bin,
                "support_count": support,
                "support_status": "PREFERRED" if support >= config.preferred_support else "THIN",
                "flow_epsilon": flow_epsilon,
                "flow_p50_threshold": flow_p50,
                "deadband": deadband,
                "lower_tail_width": lower_tail_width,
                "upper_tail_width": upper_tail_width,
                "median_change": float(median),
                "P1": float(p1),
                "P5": float(p5),
                "P95": float(p95),
                "P99": float(p99),
                "expected_direction": expected_direction,
                "stable_envelope_tight": stable_envelope_tight,
            }
        )
    return pd.DataFrame(rows)


def reason_for_row(
    row: pd.Series,
    scored: bool,
    base_risk: float,
    wrong_direction: bool,
    stable_guard_applied: bool,
    not_scored_reason: str,
) -> str:
    if not scored:
        return not_scored_reason
    if stable_guard_applied:
        return "Stable-expected group moved more than the learned stable movement limit."
    if wrong_direction and base_risk > 0:
        return (
            f"Tank moved {row['actual_direction']} while this state normally moves "
            f"{row['expected_direction']}, and the movement is outside the normal envelope."
        )
    if base_risk > 0:
        if row["robust_change_5s_percent"] < row["lower_normal"]:
            return "Tank movement is lower than the learned normal envelope for this state-flow group."
        return "Tank movement is higher than the learned normal envelope for this state-flow group."
    return "Tank movement matches learned normal dynamics for this state-flow group."


def score_rows(
    df: pd.DataFrame,
    envelopes: pd.DataFrame,
    flow_epsilon: float,
    flow_p50: float,
    deadband: float,
    config: B2Config,
) -> pd.DataFrame:
    df = df.copy()
    df["flow_bin"] = df["FIT101"].apply(lambda value: assign_flow_bin(value, flow_epsilon, flow_p50))

    env_cols = [
        "state_key",
        "flow_bin",
        "support_count",
        "lower_tail_width",
        "upper_tail_width",
        "median_change",
        "P1",
        "P5",
        "P95",
        "P99",
        "expected_direction",
        "stable_envelope_tight",
    ]
    merged = df.merge(envelopes[env_cols], on=["state_key", "flow_bin"], how="left")

    results: list[dict[str, object]] = []
    for _, row in merged.iterrows():
        row_dict = row.to_dict()
        not_scored_reason = ""
        scored = True

        if pd.isna(row_dict["robust_change_5s_percent"]):
            scored = False
            not_scored_reason = "Not scored because the 5-second robust movement is not available."
        elif bool(row_dict["boundary_excluded"]):
            scored = False
            not_scored_reason = "Not scored because row is inside actuator/phase boundary window."
        elif pd.isna(row_dict["support_count"]):
            scored = False
            not_scored_reason = "Not scored because no calibration envelope exists for this state-flow group."
        elif int(row_dict["support_count"]) < config.min_support:
            scored = False
            not_scored_reason = "Not scored because calibration support is too low for this state-flow group."

        actual = float(row_dict["robust_change_5s_percent"]) if not pd.isna(row_dict["robust_change_5s_percent"]) else np.nan
        expected = float(row_dict["median_change"]) if not pd.isna(row_dict["median_change"]) else np.nan
        lower_normal = float(row_dict["P5"]) if not pd.isna(row_dict["P5"]) else np.nan
        upper_normal = float(row_dict["P95"]) if not pd.isna(row_dict["P95"]) else np.nan
        lower_critical = float(row_dict["P1"]) if not pd.isna(row_dict["P1"]) else np.nan
        upper_critical = float(row_dict["P99"]) if not pd.isna(row_dict["P99"]) else np.nan
        lower_tail_width = float(row_dict["lower_tail_width"]) if not pd.isna(row_dict["lower_tail_width"]) else np.nan
        upper_tail_width = float(row_dict["upper_tail_width"]) if not pd.isna(row_dict["upper_tail_width"]) else np.nan

        exceedance = np.nan
        base_risk = np.nan
        model_risk = 0.0
        expected_dir = ""
        actual_dir = ""
        residual = np.nan
        stable_guard_applied = False
        wrong_dir = False

        if scored:
            residual = actual - expected
            if actual < lower_normal:
                exceedance = (lower_normal - actual) / lower_tail_width
            elif actual > upper_normal:
                exceedance = (actual - upper_normal) / upper_tail_width
            else:
                exceedance = 0.0
            base_risk = clip(float(exceedance))

            expected_dir = str(row_dict["expected_direction"])
            actual_dir = direction(actual, deadband)
            wrong_dir = is_wrong_direction(expected_dir, actual_dir)
            stable_compatible = expected_dir == "STABLE" or actual_dir == "STABLE"

            if wrong_dir and base_risk > 0:
                model_risk = 0.60 + 0.40 * base_risk
            elif wrong_dir and base_risk == 0:
                model_risk = 0.0
            else:
                model_risk = base_risk

            stable_limit = max(abs(lower_normal), abs(upper_normal), deadband)
            if (
                expected_dir == "STABLE"
                and not bool(row_dict["stable_envelope_tight"])
                and abs(actual) > stable_limit
            ):
                model_risk = max(model_risk, 0.60)
                stable_guard_applied = True

            model_risk = clip(float(model_risk))
        else:
            expected_dir = ""
            actual_dir = ""

        reason_context = {
            "actual_direction": actual_dir,
            "expected_direction": expected_dir,
            "robust_change_5s_percent": actual,
            "lower_normal": lower_normal,
        }
        out = {
            "Timestamp": row_dict["Timestamp"],
            "Normal/Attack": row_dict["Normal/Attack"],
            "event_type": row_dict["event_type"],
            "process_phase": row_dict["process_phase"],
            "state_key": row_dict["state_key"],
            "flow_bin": row_dict["flow_bin"],
            "support_count": int(row_dict["support_count"]) if not pd.isna(row_dict["support_count"]) else "",
            "exceedance": round(float(exceedance), 6) if scored else np.nan,
            "expected_direction": expected_dir if scored else np.nan,
            "actual_direction": actual_dir if scored else np.nan,
            "scored_flag": bool(scored),
            "is_command_row": bool(row_dict["is_command_row"]),
            "LIT101": round(float(row_dict["LIT101"]), 4),
            "FIT101": round(float(row_dict["FIT101"]), 6),
            "robust_change_5s_percent": round(actual, 6) if not pd.isna(actual) else np.nan,
            "expected_change_5s_percent": round(expected, 6) if scored else np.nan,
            "lower_normal": round(lower_normal, 6) if scored else np.nan,
            "upper_normal": round(upper_normal, 6) if scored else np.nan,
            "lower_critical": round(lower_critical, 6) if scored else np.nan,
            "upper_critical": round(upper_critical, 6) if scored else np.nan,
            "dynamics_residual": round(float(residual), 6) if scored else np.nan,
            "base_risk": round(float(base_risk), 6) if scored else np.nan,
            "model_risk": round(model_risk, 6),
            "model_level": risk_level(model_risk, scored),
            "model_reason": reason_for_row(
                pd.Series(reason_context),
                scored,
                0.0 if pd.isna(base_risk) else float(base_risk),
                wrong_dir,
                stable_guard_applied,
                not_scored_reason,
            ),
        }
        results.append(out)

    return pd.DataFrame(results)


def make_metric_rows(scored: pd.DataFrame, validation_rows: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    threshold = 0.5

    subsets = {
        "Normal validation all rows": validation_rows,
        "Normal validation command rows": validation_rows[validation_rows["is_command_row"]],
        "Attack all rows": scored[scored["Normal/Attack"] == "Attack"],
        "Attack command rows": scored[
            (scored["Normal/Attack"] == "Attack") & scored["is_command_row"]
        ],
    }
    for name, subset in subsets.items():
        numerator = int((subset["model_risk"] >= threshold).sum())
        denominator = int(len(subset))
        rows.append(
            {
                "metric": f"{name} with model_risk >= {threshold}",
                "numerator": numerator,
                "denominator": denominator,
                "percent": (numerator / denominator * 100.0) if denominator else 0.0,
            }
        )

    for label in ["Normal", "Attack"]:
        subset = scored[scored["Normal/Attack"] == label]
        rows.append(
            {
                "metric": f"{label} mean model_risk",
                "numerator": subset["model_risk"].mean(),
                "denominator": len(subset),
                "percent": "",
            }
        )
        rows.append(
            {
                "metric": f"{label} median model_risk",
                "numerator": subset["model_risk"].median(),
                "denominator": len(subset),
                "percent": "",
            }
        )

    rows.append(
        {
            "metric": "NOT_SCORED rows",
            "numerator": int((~scored["scored_flag"]).sum()),
            "denominator": len(scored),
            "percent": ((~scored["scored_flag"]).mean() * 100.0) if len(scored) else 0.0,
        }
    )
    low_support = scored[scored["model_reason"].str.contains("support is too low", na=False)]
    rows.append(
        {
            "metric": "Low-support NOT_SCORED rows",
            "numerator": len(low_support),
            "denominator": len(scored),
            "percent": (len(low_support) / len(scored) * 100.0) if len(scored) else 0.0,
        }
    )
    return pd.DataFrame(rows)


def make_phase_summary(scored: pd.DataFrame, output_path: Path) -> None:
    rows = []
    for (label, phase), group in scored.groupby(["Normal/Attack", "process_phase"]):
        rows.append(
            {
                "label": label,
                "process_phase": phase,
                "rows": len(group),
                "scored_rows": int(group["scored_flag"].sum()),
                "risk_ge_0_5": int((group["model_risk"] >= 0.5).sum()),
                "risk_ge_0_5_percent": (
                    (group["model_risk"] >= 0.5).mean() * 100.0
                    if len(group)
                    else 0.0
                ),
                "mean_model_risk": group["model_risk"].mean(),
            }
        )
    pd.DataFrame(rows).to_csv(output_path, index=False)


def make_examples(scored: pd.DataFrame, b1_path: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    top = scored[
        (scored["scored_flag"])
        & (scored["model_risk"] > 0)
    ].sort_values(["model_risk", "exceedance"], ascending=False).head(50)

    cases = scored[
        (scored["Normal/Attack"] == "Attack")
        & (scored["scored_flag"])
        & (scored["model_risk"] > 0)
    ].sort_values(["model_risk", "exceedance"], ascending=False).head(25)

    if b1_path and b1_path.exists() and not cases.empty:
        b1 = pd.read_csv(b1_path)
        timestamp_col = "timestamp" if "timestamp" in b1.columns else "Timestamp"
        b1[timestamp_col] = pd.to_datetime(b1[timestamp_col])
        cols = [timestamp_col]
        for col in ["decision", "final_risk", "physics_risk", "sensor_risk"]:
            if col in b1.columns:
                cols.append(col)
        cases = cases.merge(
            b1[cols],
            left_on="Timestamp",
            right_on=timestamp_col,
            how="left",
        ).drop(columns=[timestamp_col])
    return top, cases


def plot_outputs(scored: pd.DataFrame, envelopes: pd.DataFrame, plot_dir: Path) -> None:
    plot_dir.mkdir(parents=True, exist_ok=True)

    scored_only = scored[scored["scored_flag"]].copy()
    fig, ax = plt.subplots(figsize=(9, 5))
    for label, color in [("Normal", "#2563eb"), ("Attack", "#dc2626")]:
        values = scored_only[scored_only["Normal/Attack"] == label]["model_risk"]
        ax.hist(values, bins=30, alpha=0.55, density=True, label=label, color=color)
    ax.set_title("B2 Model Risk Distribution")
    ax.set_xlabel("model_risk")
    ax.set_ylabel("Density")
    ax.legend()
    ax.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(plot_dir / "b2_model_risk_distribution.png", dpi=170)
    plt.close()

    phase = (
        scored.groupby(["process_phase", "Normal/Attack"])["model_risk"]
        .mean()
        .unstack(fill_value=0)
    )
    fig, ax = plt.subplots(figsize=(10, 5))
    phase.plot(kind="bar", ax=ax, color=["#dc2626", "#2563eb"])
    ax.set_title("B2 Mean Model Risk By Phase")
    ax.set_xlabel("Process phase")
    ax.set_ylabel("Mean model_risk")
    ax.tick_params(axis="x", rotation=25)
    ax.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(plot_dir / "b2_phase_risk_summary.png", dpi=170)
    plt.close()

    env_plot = envelopes.sort_values("support_count", ascending=False).head(12).copy()
    env_plot["group"] = env_plot["state_key"] + "\n" + env_plot["flow_bin"]
    x = np.arange(len(env_plot))
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.vlines(x, env_plot["P1"], env_plot["P99"], color="#94a3b8", linewidth=5, label="P1-P99")
    ax.vlines(x, env_plot["P5"], env_plot["P95"], color="#2563eb", linewidth=10, label="P5-P95")
    ax.scatter(x, env_plot["median_change"], color="#dc2626", zorder=5, label="Median")
    ax.set_xticks(x)
    ax.set_xticklabels(env_plot["group"], fontsize=8)
    ax.set_title("B2 Learned Level-Change Envelopes")
    ax.set_ylabel("robust_change_5s_percent")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(plot_dir / "b2_level_change_envelopes.png", dpi=170)
    plt.close()

    top = scored_only.sort_values("model_risk", ascending=False).head(25)
    fig, ax = plt.subplots(figsize=(11, 5))
    colors = np.where(top["Normal/Attack"].eq("Attack"), "#dc2626", "#64748b")
    ax.bar(np.arange(len(top)), top["model_risk"], color=colors)
    ax.set_title("B2 Top Abnormal Examples")
    ax.set_xlabel("Top rows")
    ax.set_ylabel("model_risk")
    ax.set_ylim(0, 1.05)
    ax.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(plot_dir / "b2_top_abnormal_examples.png", dpi=170)
    plt.close()


def run(input_path: Path, output_dir: Path, b1_path: Path | None, config: B2Config) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_dir = output_dir.parent / "plots" / "b2_dynamics"

    df = add_state_and_motion_features(load_input(input_path), config)
    calibration, validation, _attack = normal_calibration_split(df)
    flow_epsilon, flow_p50 = fit_flow_bins(calibration)
    df["flow_bin"] = df["FIT101"].apply(lambda value: assign_flow_bin(value, flow_epsilon, flow_p50))
    calibration["flow_bin"] = calibration["FIT101"].apply(lambda value: assign_flow_bin(value, flow_epsilon, flow_p50))
    deadband = fit_deadband(calibration)
    envelopes = fit_envelopes(calibration, flow_epsilon, flow_p50, deadband, config)
    scored = score_rows(df, envelopes, flow_epsilon, flow_p50, deadband, config)

    validation_timestamps = set(validation["Timestamp"])
    validation_scored = scored[scored["Timestamp"].isin(validation_timestamps)].copy()

    scored.to_csv(output_dir / "swat_b2_dynamics_results.csv", index=False)
    envelopes.to_csv(output_dir / "b2_dynamics_group_envelopes.csv", index=False)
    make_metric_rows(scored, validation_scored).to_csv(
        output_dir / "b2_dynamics_metrics.csv",
        index=False,
    )
    make_phase_summary(scored, output_dir / "b2_dynamics_phase_summary.csv")
    top, cases = make_examples(scored, b1_path)
    top.to_csv(output_dir / "b2_dynamics_top_examples.csv", index=False)
    cases.to_csv(output_dir / "b2_dynamics_case_studies.csv", index=False)
    plot_outputs(scored, envelopes, plot_dir)

    print("B2 complete")
    print(f"Rows scored: {int(scored['scored_flag'].sum())} / {len(scored)}")
    print(f"Flow epsilon: {flow_epsilon:.6f}")
    print(f"Flow P50 threshold: {flow_p50:.6f}")
    print(f"Deadband: {deadband:.6f}")
    print(f"Wrote results to {output_dir / 'swat_b2_dynamics_results.csv'}")
    print(f"Wrote plots to {plot_dir}")
    print()
    print(pd.read_csv(output_dir / "b2_dynamics_metrics.csv").to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("processed/swat_stage1_context_features.csv"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("processed"))
    parser.add_argument("--b1-results", type=Path, default=Path("processed/swat_b1_final_results.csv"))
    parser.add_argument("--change-horizon-samples", type=int, default=5)
    parser.add_argument("--smoothing-window-samples", type=int, default=9)
    parser.add_argument("--min-support", type=int, default=100)
    args = parser.parse_args()

    config = B2Config(
        change_horizon_samples=args.change_horizon_samples,
        smoothing_window_samples=args.smoothing_window_samples,
        min_support=args.min_support,
    )
    run(args.input, args.output_dir, args.b1_results, config)


if __name__ == "__main__":
    main()
