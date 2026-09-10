"""
PICACA-Lite Student 2:
B3 safety-preserving hybrid fusion model for SWaT Stage 1.

B3 combines B1 physics/sensor risk, B2 effective dynamics risk, and Yash V3
weighted context risk. It preserves B1 as the minimum safety baseline, so the
fusion decision can only raise severity and never downgrade B1.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DECISION_ORDER = ["ALLOW", "DELAY_ALERT", "BLOCK_ALARM"]
SEVERITY = {"ALLOW": 0, "DELAY_ALERT": 1, "BLOCK_ALARM": 2}
SEVERITY_TO_DECISION = {value: key for key, value in SEVERITY.items()}
COLORS = {"ALLOW": "#2563eb", "DELAY_ALERT": "#f59e0b", "BLOCK_ALARM": "#dc2626"}


@dataclass(frozen=True)
class B3Config:
    physics_weight: float = 0.35
    sensor_weight: float = 0.20
    b2_weight: float = 0.25
    context_weight: float = 0.20
    delay_threshold: float = 0.55
    block_threshold: float = 0.85
    hard_gate_threshold: float = 0.85
    agreement_threshold: float = 0.50
    b2_lookback_seconds: int = 30


def max_severity(decision_a: str, decision_b: str) -> str:
    return SEVERITY_TO_DECISION[max(SEVERITY[decision_a], SEVERITY[decision_b])]


def load_csv(path: Path, timestamp_column: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if timestamp_column not in df.columns:
        raise ValueError(f"{path} is missing timestamp column {timestamp_column}")
    df = df.rename(columns={timestamp_column: "Timestamp"}).copy()
    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    return df.sort_values("Timestamp").reset_index(drop=True)


def assert_identical_timestamps(b1: pd.DataFrame, b2: pd.DataFrame, v3: pd.DataFrame) -> None:
    if not (len(b1) == len(b2) == len(v3)):
        raise ValueError(f"Row count mismatch: B1={len(b1)}, B2={len(b2)}, V3={len(v3)}")
    if not b1["Timestamp"].equals(b2["Timestamp"]):
        raise ValueError("B1 and B2 Timestamp coverage/order do not match.")
    if not b1["Timestamp"].equals(v3["Timestamp"]):
        raise ValueError("B1 and V3 Timestamp coverage/order do not match.")


def assert_command_rows_match(merged: pd.DataFrame) -> None:
    b1_command = merged["command_type"].astype(str).ne("NO_COMMAND")
    b2_command = merged["event_type_b2"].astype(str).ne("NONE")
    v3_command = merged["event_type_v3"].astype(str).ne("NONE")
    mismatch = merged[~((b1_command == b2_command) & (b1_command == v3_command))]
    if not mismatch.empty:
        sample = mismatch[["Timestamp", "command_type", "event_type_b2", "event_type_v3"]].head(10)
        raise ValueError(f"Command-row mismatch across files. Sample:\n{sample.to_string(index=False)}")


def merge_inputs(b1_path: Path, b2_path: Path, v3_path: Path) -> pd.DataFrame:
    b1 = load_csv(b1_path, "timestamp")
    b2 = load_csv(b2_path, "Timestamp")
    v3 = load_csv(v3_path, "Timestamp")

    assert_identical_timestamps(b1, b2, v3)

    b1_cols = [
        "Timestamp",
        "label",
        "command_type",
        "level_percent",
        "projected_level",
        "physics_risk",
        "sensor_risk",
        "decision",
        "reason",
    ]
    b2_cols = [
        "Timestamp",
        "event_type",
        "model_risk",
        "scored_flag",
        "model_level",
        "model_reason",
    ]
    v3_cols = [
        "Timestamp",
        "event_type",
        "context_risk_weighted",
        "context_risk_max",
        "context_level_weighted",
        "context_reason",
    ]
    for name, frame, cols in [("B1", b1, b1_cols), ("B2", b2, b2_cols), ("V3", v3, v3_cols)]:
        missing = [col for col in cols if col not in frame.columns]
        if missing:
            raise ValueError(f"{name} missing required columns: {missing}")

    merged = (
        b1[b1_cols]
        .merge(b2[b2_cols], on="Timestamp", how="inner", suffixes=("", "_b2"))
        .merge(v3[v3_cols], on="Timestamp", how="inner", suffixes=("_b2", "_v3"))
    )
    if len(merged) != len(b1):
        raise ValueError("Inner join changed row count; timestamp coverage is not identical.")

    merged["context_risk_weighted"] = merged["context_risk_weighted"].fillna(0.0)
    merged["context_risk_max"] = merged["context_risk_max"].fillna(0.0)
    merged["context_reason"] = merged["context_reason"].fillna("MISSING_CONTEXT_TREATED_AS_NEUTRAL")
    assert_command_rows_match(merged)
    return merged


def second_highest(values: np.ndarray) -> float:
    elevated = values[~np.isnan(values)]
    elevated = elevated[elevated >= 0.5]
    if len(elevated) < 2:
        return 0.0
    return float(np.sort(elevated)[-2])


def add_b2_effective_risk(df: pd.DataFrame, config: B3Config) -> pd.DataFrame:
    df = df.copy()
    if df["scored_flag"].dtype == bool:
        scored = df["scored_flag"]
    else:
        scored = df["scored_flag"].astype(str).str.lower().isin(["true", "1", "yes"])
    current = df["model_risk"].astype(float)
    recent_candidates = current.where(scored & (current >= config.agreement_threshold), np.nan)
    recent_second = (
        recent_candidates.shift(1)
        .rolling(window=config.b2_lookback_seconds, min_periods=1)
        .apply(second_highest, raw=True)
        .fillna(0.0)
    )
    df["b2_recent_model_risk_30s"] = recent_second.round(6)
    df["b2_effective_risk"] = np.where(scored, current, recent_second).round(6)
    df["b2_effective_source"] = np.where(scored, "CURRENT_SCORED", "RECENT_30S")
    df.loc[(~scored) & (df["b2_effective_risk"] == 0.0), "b2_effective_source"] = "NEUTRAL_NOT_SCORED"
    return df


def normalized_weights(active: tuple[str, ...], config: B3Config) -> dict[str, float]:
    base = {
        "physics": config.physics_weight,
        "sensor": config.sensor_weight,
        "b2": config.b2_weight,
        "context": config.context_weight,
    }
    total = sum(base[name] for name in active)
    return {name: base[name] / total for name in active}


def compute_score(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    score = np.zeros(len(df), dtype=float)
    if "physics" in weights:
        score += weights["physics"] * df["physics_risk"].astype(float)
    if "sensor" in weights:
        score += weights["sensor"] * df["sensor_risk"].astype(float)
    if "b2" in weights:
        score += weights["b2"] * df["b2_effective_risk"].astype(float)
    if "context" in weights:
        score += weights["context"] * df["context_risk_weighted"].astype(float)
    return pd.Series(np.clip(score, 0.0, 1.0), index=df.index)


def decide_fusion(
    b1_decision: str,
    command_row: bool,
    physics_risk: float,
    sensor_risk: float,
    b2_effective_risk: float,
    context_risk: float,
    fusion_score: float,
    config: B3Config,
) -> tuple[str, str]:
    if b1_decision == "BLOCK_ALARM":
        return "BLOCK_ALARM", "B1 already blocked the command."
    if command_row and (
        physics_risk >= config.hard_gate_threshold or sensor_risk >= config.hard_gate_threshold
    ):
        return "BLOCK_ALARM", "Command row has high B1 physics or sensor risk."
    if physics_risk >= config.hard_gate_threshold or sensor_risk >= config.hard_gate_threshold:
        return max_severity(b1_decision, "DELAY_ALERT"), "Monitoring row has high B1 physics or sensor risk."
    if command_row and fusion_score >= config.block_threshold:
        return "BLOCK_ALARM", "Command row has high fused B3 score."
    if fusion_score >= config.block_threshold:
        return max_severity(b1_decision, "DELAY_ALERT"), "Monitoring row has high fused B3 score."
    if fusion_score >= config.delay_threshold:
        return max_severity(b1_decision, "DELAY_ALERT"), "B3 fused score reached delay threshold."
    if (
        context_risk >= config.agreement_threshold
        and b2_effective_risk >= config.agreement_threshold
    ):
        return max_severity(b1_decision, "DELAY_ALERT"), "B2 dynamics and context risk both agree."
    return b1_decision, "B3 did not add enough evidence beyond B1."


def apply_decisions(
    df: pd.DataFrame,
    score_col: str,
    decision_col: str,
    reason_col: str,
    config: B3Config,
) -> pd.DataFrame:
    decisions: list[str] = []
    reasons: list[str] = []
    command_rows = df["command_type"].astype(str).ne("NO_COMMAND")
    for row, command_row in zip(df.itertuples(index=False), command_rows):
        item = row._asdict()
        decision, reason = decide_fusion(
            b1_decision=item["decision"],
            command_row=bool(command_row),
            physics_risk=float(item["physics_risk"]),
            sensor_risk=float(item["sensor_risk"]),
            b2_effective_risk=float(item.get("b2_effective_risk", 0.0)),
            context_risk=float(item.get("context_risk_weighted", 0.0)),
            fusion_score=float(item[score_col]),
            config=config,
        )
        decisions.append(decision)
        reasons.append(reason)
    df[decision_col] = decisions
    df[reason_col] = reasons
    df[f"{decision_col}_severity"] = df[decision_col].map(SEVERITY)
    return df


def add_full_b3(df: pd.DataFrame, config: B3Config) -> pd.DataFrame:
    df = df.copy()
    weights = normalized_weights(("physics", "sensor", "b2", "context"), config)
    df["b3_score"] = compute_score(df, weights).round(6)
    df = apply_decisions(df, "b3_score", "b3_decision", "b3_reason", config)
    b1_sev = df["decision"].map(SEVERITY)
    if (df["b3_decision_severity"] < b1_sev).any():
        raise ValueError("Monotonicity violation: B3 downgraded at least one B1 decision.")
    df["b3_raised_b1"] = df["b3_decision_severity"] > b1_sev
    return df


def add_ablation_decisions(df: pd.DataFrame, config: B3Config) -> pd.DataFrame:
    variants = {
        "b1_b2": ("physics", "sensor", "b2"),
        "b1_context": ("physics", "sensor", "context"),
    }
    out = df.copy()
    for prefix, active in variants.items():
        out[f"{prefix}_score"] = compute_score(out, normalized_weights(active, config)).round(6)
        out = apply_decisions(out, f"{prefix}_score", f"{prefix}_decision", f"{prefix}_reason", config)
    return out


def normal_validation_mask(df: pd.DataFrame) -> pd.Series:
    normal_idx = df.index[df["label"] == "Normal"]
    cut = int(len(normal_idx) * 0.80)
    validation_idx = set(normal_idx[cut:])
    return df.index.to_series().isin(validation_idx)


def metric_rows(df: pd.DataFrame, decision_col: str, label: str) -> list[dict[str, object]]:
    command = df[df["command_type"] != "NO_COMMAND"]
    normal = df[df["label"] == "Normal"]
    attack = df[df["label"] == "Attack"]
    normal_cmd = command[command["label"] == "Normal"]
    attack_cmd = command[command["label"] == "Attack"]

    def pct(num: int, den: int) -> float:
        return (num / den * 100.0) if den else 0.0

    rows = []
    for scope, subset, predicate, meaning in [
        ("All rows", normal, normal[decision_col] != "ALLOW", "Normal rows delayed or blocked."),
        ("All rows", attack, attack[decision_col] != "ALLOW", "Attack-period rows delayed or blocked."),
        (
            "Command rows",
            normal_cmd,
            normal_cmd[decision_col] != "ALLOW",
            "Normal command rows delayed or blocked.",
        ),
        (
            "Command rows",
            attack_cmd,
            attack_cmd[decision_col] != "ALLOW",
            "Attack command rows delayed or blocked.",
        ),
    ]:
        numerator = int(predicate.sum())
        rows.append(
            {
                "variant": label,
                "scope": scope,
                "subset": "Normal" if "Normal" in meaning else "Attack",
                "numerator": numerator,
                "denominator": len(subset),
                "percent": pct(numerator, len(subset)),
                "meaning": meaning,
            }
        )
    return rows


def decision_table(df: pd.DataFrame, decision_col: str) -> pd.DataFrame:
    return pd.crosstab(df["label"], df[decision_col]).reindex(
        index=["Normal", "Attack"],
        columns=DECISION_ORDER,
        fill_value=0,
    )


def generate_metrics(df: pd.DataFrame, output_dir: Path) -> None:
    rows = []
    rows.extend(metric_rows(df.rename(columns={"decision": "b1_decision_tmp"}), "b1_decision_tmp", "B1"))
    rows.extend(metric_rows(df, "b1_b2_decision", "B1+B2"))
    rows.extend(metric_rows(df, "b1_context_decision", "B1+Context"))
    rows.extend(metric_rows(df, "b3_decision", "Full B3"))
    pd.DataFrame(rows).to_csv(output_dir / "b3_metrics_summary.csv", index=False)

    ablation_rows = []
    for variant, decision_col, score_col in [
        ("B1", "decision", None),
        ("B1+B2", "b1_b2_decision", "b1_b2_score"),
        ("B1+Context", "b1_context_decision", "b1_context_score"),
        ("Full B3", "b3_decision", "b3_score"),
    ]:
        table = decision_table(df, decision_col)
        for label_name, row in table.iterrows():
            record = {"variant": variant, "label": label_name}
            record.update({decision: int(row[decision]) for decision in DECISION_ORDER})
            if score_col:
                record["mean_score"] = df[df["label"] == label_name][score_col].mean()
            ablation_rows.append(record)
    pd.DataFrame(ablation_rows).to_csv(output_dir / "b3_ablation_summary.csv", index=False)


def generate_threshold_sweep(df: pd.DataFrame, config: B3Config, output_path: Path) -> None:
    validation = df[normal_validation_mask(df)].copy()
    all_rows = []
    for delay in [0.45, 0.50, 0.55, 0.60, 0.65]:
        for block in [0.80, 0.85, 0.90]:
            local_config = B3Config(
                physics_weight=config.physics_weight,
                sensor_weight=config.sensor_weight,
                b2_weight=config.b2_weight,
                context_weight=config.context_weight,
                delay_threshold=delay,
                block_threshold=block,
                hard_gate_threshold=config.hard_gate_threshold,
                agreement_threshold=config.agreement_threshold,
                b2_lookback_seconds=config.b2_lookback_seconds,
            )
            temp = validation.copy()
            temp = apply_decisions(temp, "b3_score", "tmp_decision", "tmp_reason", local_config)
            command = temp[temp["command_type"] != "NO_COMMAND"]
            all_rows.append(
                {
                    "delay_threshold": delay,
                    "block_threshold": block,
                    "normal_validation_nonallow": int((temp["tmp_decision"] != "ALLOW").sum()),
                    "normal_validation_rows": len(temp),
                    "normal_validation_nonallow_percent": (temp["tmp_decision"] != "ALLOW").mean() * 100.0,
                    "normal_validation_command_nonallow": int((command["tmp_decision"] != "ALLOW").sum()),
                    "normal_validation_command_rows": len(command),
                    "normal_validation_command_nonallow_percent": (
                        (command["tmp_decision"] != "ALLOW").mean() * 100.0 if len(command) else 0.0
                    ),
                }
            )
    pd.DataFrame(all_rows).to_csv(output_path, index=False)


def generate_weight_sensitivity(df: pd.DataFrame, config: B3Config, output_path: Path) -> None:
    base_weights = {
        "physics": config.physics_weight,
        "sensor": config.sensor_weight,
        "b2": config.b2_weight,
        "context": config.context_weight,
    }
    rows = []
    variants = [("base", None, 0.0)]
    for component in base_weights:
        for delta in [-0.10, 0.10]:
            variants.append((f"{component}_{delta:+.2f}", component, delta))

    for name, component, delta in variants:
        weights = base_weights.copy()
        if component is not None:
            weights[component] = max(0.01, weights[component] + delta)
        total = sum(weights.values())
        weights = {key: value / total for key, value in weights.items()}
        temp = df.copy()
        temp["tmp_score"] = compute_score(temp, weights)
        temp = apply_decisions(temp, "tmp_score", "tmp_decision", "tmp_reason", config)
        command = temp[temp["command_type"] != "NO_COMMAND"]
        normal_cmd = command[command["label"] == "Normal"]
        attack_cmd = command[command["label"] == "Attack"]
        rows.append(
            {
                "variant": name,
                "physics_weight": weights["physics"],
                "sensor_weight": weights["sensor"],
                "b2_weight": weights["b2"],
                "context_weight": weights["context"],
                "normal_command_nonallow": int((normal_cmd["tmp_decision"] != "ALLOW").sum()),
                "attack_command_nonallow": int((attack_cmd["tmp_decision"] != "ALLOW").sum()),
                "all_rows_changed_vs_b3": int((temp["tmp_decision"] != df["b3_decision"]).sum()),
            }
        )
    pd.DataFrame(rows).to_csv(output_path, index=False)


def generate_case_studies(df: pd.DataFrame, output_path: Path) -> None:
    raised_attack = df[(df["label"] == "Attack") & (df["b3_raised_b1"])].copy()
    top_attack = df[(df["label"] == "Attack")].sort_values(
        ["b3_decision_severity", "b3_score", "b2_effective_risk", "context_risk_weighted"],
        ascending=False,
    )
    cases = pd.concat([raised_attack.head(30), top_attack.head(30)], ignore_index=True).drop_duplicates(
        subset=["Timestamp"]
    )
    cols = [
        "Timestamp",
        "label",
        "command_type",
        "level_percent",
        "projected_level",
        "physics_risk",
        "sensor_risk",
        "b2_effective_risk",
        "b2_effective_source",
        "context_risk_weighted",
        "context_risk_max",
        "b3_score",
        "decision",
        "b3_decision",
        "b3_reason",
        "model_reason",
        "context_reason",
        "reason",
    ]
    cases[[col for col in cols if col in cases.columns]].to_csv(output_path, index=False)


def plot_outputs(df: pd.DataFrame, output_dir: Path) -> None:
    plot_dir = output_dir.parent / "plots" / "b3_fusion"
    plot_dir.mkdir(parents=True, exist_ok=True)

    table = decision_table(df, "b3_decision")
    fig, ax = plt.subplots(figsize=(8, 5))
    table.plot(kind="bar", ax=ax, color=[COLORS[item] for item in DECISION_ORDER])
    ax.set_title("B3 Decision Distribution")
    ax.set_xlabel("Label")
    ax.set_ylabel("Rows")
    ax.tick_params(axis="x", rotation=0)
    for container in ax.containers:
        ax.bar_label(container, fontsize=8)
    plt.tight_layout()
    plt.savefig(plot_dir / "b3_decision_distribution.png", dpi=170)
    plt.close()

    ablation_counts = []
    for variant, col in [
        ("B1", "decision"),
        ("B1+B2", "b1_b2_decision"),
        ("B1+Context", "b1_context_decision"),
        ("Full B3", "b3_decision"),
    ]:
        command = df[df["command_type"] != "NO_COMMAND"]
        attack_cmd = command[command["label"] == "Attack"]
        normal_cmd = command[command["label"] == "Normal"]
        ablation_counts.append(
            {
                "variant": variant,
                "normal_command_nonallow": int((normal_cmd[col] != "ALLOW").sum()),
                "attack_command_nonallow": int((attack_cmd[col] != "ALLOW").sum()),
            }
        )
    ablation_df = pd.DataFrame(ablation_counts).set_index("variant")
    fig, ax = plt.subplots(figsize=(9, 5))
    ablation_df.plot(kind="bar", ax=ax, color=["#64748b", "#dc2626"])
    ax.set_title("B3 Ablation Comparison: Command Rows")
    ax.set_xlabel("Variant")
    ax.set_ylabel("Non-ALLOW command rows")
    ax.tick_params(axis="x", rotation=20)
    for container in ax.containers:
        ax.bar_label(container, fontsize=8)
    plt.tight_layout()
    plt.savefig(plot_dir / "b3_ablation_comparison.png", dpi=170)
    plt.close()

    fig, ax = plt.subplots(figsize=(9, 5))
    for label, color in [("Normal", "#2563eb"), ("Attack", "#dc2626")]:
        ax.hist(df[df["label"] == label]["b3_score"], bins=40, alpha=0.55, density=True, label=label, color=color)
    ax.axvline(0.55, color="#f59e0b", linestyle="--", label="Delay threshold")
    ax.axvline(0.85, color="#dc2626", linestyle="--", label="Block threshold")
    ax.set_title("B3 Score Distribution")
    ax.set_xlabel("b3_score")
    ax.set_ylabel("Density")
    ax.legend()
    ax.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(plot_dir / "b3_score_distribution.png", dpi=170)
    plt.close()

    sensitivity_path = output_dir / "b3_weight_sensitivity.csv"
    if sensitivity_path.exists():
        sens = pd.read_csv(sensitivity_path)
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(sens["variant"], sens["all_rows_changed_vs_b3"], color="#0f766e")
        ax.set_title("B3 Weight Sensitivity")
        ax.set_xlabel("Weight variant")
        ax.set_ylabel("Rows changed vs base B3")
        ax.tick_params(axis="x", rotation=45)
        plt.tight_layout()
        plt.savefig(plot_dir / "b3_weight_sensitivity.png", dpi=170)
        plt.close()


def run(b1_path: Path, b2_path: Path, v3_path: Path, output_dir: Path, config: B3Config) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = merge_inputs(b1_path, b2_path, v3_path)
    df = add_b2_effective_risk(df, config)
    df = add_full_b3(df, config)
    df = add_ablation_decisions(df, config)

    output_cols = [
        "Timestamp",
        "label",
        "command_type",
        "level_percent",
        "projected_level",
        "physics_risk",
        "sensor_risk",
        "model_risk",
        "scored_flag",
        "b2_recent_model_risk_30s",
        "b2_effective_risk",
        "b2_effective_source",
        "context_risk_weighted",
        "context_risk_max",
        "context_level_weighted",
        "b3_score",
        "decision",
        "b3_decision",
        "b3_raised_b1",
        "b3_reason",
        "model_level",
        "model_reason",
        "context_reason",
        "reason",
        "b1_b2_score",
        "b1_b2_decision",
        "b1_context_score",
        "b1_context_decision",
    ]
    df[output_cols].to_csv(output_dir / "swat_b3_fusion_results.csv", index=False)
    generate_metrics(df, output_dir)
    generate_threshold_sweep(df, config, output_dir / "b3_threshold_sweep.csv")
    generate_weight_sensitivity(df, config, output_dir / "b3_weight_sensitivity.csv")
    generate_case_studies(df, output_dir / "b3_case_studies.csv")
    plot_outputs(df, output_dir)

    print("B3 complete")
    print(f"Rows: {len(df):,}")
    print(f"B3 raised B1 on {int(df['b3_raised_b1'].sum()):,} rows")
    print()
    print("B3 decision table")
    print(decision_table(df, "b3_decision").to_string())
    print()
    print(pd.read_csv(output_dir / "b3_metrics_summary.csv").to_string(index=False))
    print(f"\nWrote B3 outputs to {output_dir}")
    print(f"Wrote B3 plots to {output_dir.parent / 'plots' / 'b3_fusion'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--b1", type=Path, default=Path("processed/swat_b1_final_results.csv"))
    parser.add_argument("--b2", type=Path, default=Path("processed/swat_b2_dynamics_results.csv"))
    parser.add_argument("--v3", type=Path, default=Path("processed/swat_stage1_context_risk_v3.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("processed"))
    args = parser.parse_args()
    run(args.b1, args.b2, args.v3, args.output_dir, B3Config())


if __name__ == "__main__":
    main()
