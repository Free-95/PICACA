import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

PROJECT_DIR = r"e:\ISA_PROJECT"
INPUT_EVENTS = os.path.join(PROJECT_DIR, "processed", "swat_stage1_events.csv")
OUTPUT_FEATURES = os.path.join(PROJECT_DIR, "processed", "swat_stage1_context_features.csv")
PLOTS_DIR = os.path.join(PROJECT_DIR, "plots", "context_features")

def build_context_features(df, w_sec=300, w_short_sec=60):
    """
    Build command-context features from Stage 1 events and process dynamics.
    """
    df = df.copy()
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    
    # 1. Event Binary Indicators
    df['is_event'] = (df['event_type'] != 'NONE').astype(int)
    df['is_p101_event'] = df['event_type'].str.contains('P101').astype(int)
    df['is_p102_event'] = df['event_type'].str.contains('P102').astype(int)
    df['is_mv101_event'] = df['event_type'].str.contains('MV101').astype(int)
    
    # 2. Command Frequency Features (Rolling sums over time window)
    df['command_frequency'] = df['is_event'].rolling(window=w_sec, min_periods=1).sum().astype(int)
    df['command_frequency_60s'] = df['is_event'].rolling(window=w_short_sec, min_periods=1).sum().astype(int)
    
    df['p101_frequency'] = df['is_p101_event'].rolling(window=w_sec, min_periods=1).sum().astype(int)
    df['p102_frequency'] = df['is_p102_event'].rolling(window=w_sec, min_periods=1).sum().astype(int)
    df['mv101_frequency'] = df['is_mv101_event'].rolling(window=w_sec, min_periods=1).sum().astype(int)
    
    # 3. Command Repetition Features
    event_type_dummies = pd.get_dummies(df['event_type'])
    if 'NONE' in event_type_dummies.columns:
        event_type_dummies = event_type_dummies.drop(columns=['NONE'])
        
    rolling_type_counts = event_type_dummies.rolling(window=w_sec, min_periods=1).sum()
    df['command_repetition'] = rolling_type_counts.max(axis=1).astype(int)
    
    consec = []
    curr_type = None
    count = 0
    for et in df['event_type']:
        if et != 'NONE':
            if et == curr_type:
                count += 1
            else:
                curr_type = et
                count = 1
            consec.append(count)
        else:
            consec.append(0)
    df['consecutive_same_command'] = consec
    
    # 4. Command-Value Change Features
    def encode_value_change(row):
        et = row['event_type']
        if et == 'NONE':
            return 0
        elif ';' in et:
            return 2 # Compound event
        else:
            return 1 # Standard single actuator event
            
    df['command_value_change'] = df.apply(encode_value_change, axis=1)
    df['command_value_change_desc'] = df['event_type'] + ":" + df['event_value']
    
    # 5. Tank Level Trend Features & Spike-Robust Slope
    lit101_shift_w = df['LIT101'].shift(w_sec).bfill()
    df['level_slope_300s'] = (df['LIT101'] - lit101_shift_w) / w_sec
    
    df['robust_level_slope'] = df['level_change'].rolling(window=w_sec, min_periods=1).median()
    
    def get_trend_dir(slope):
        if slope > 0.05:
            return 'RISING'
        elif slope < -0.05:
            return 'FALLING'
        else:
            return 'STABLE'
            
    df['level_trend_direction'] = df['robust_level_slope'].apply(get_trend_dir)
    
    # 6. Process Phase Determination
    def determine_phase(row):
        mv_state = row['MV101_state']
        pump_on = (row['P101_state'] == 1) or (row['P102_state'] == 1)
        
        if mv_state == 'Transition':
            return 'TRANSITIONING'
        elif mv_state == 'Open' and not pump_on:
            return 'FILLING'
        elif mv_state == 'Closed' and pump_on:
            return 'DRAINING'
        elif mv_state == 'Open' and pump_on:
            return 'TRANSFERRING'
        elif mv_state == 'Closed' and not pump_on:
            return 'HOLDING'
        else:
            return 'UNKNOWN'
            
    df['process_phase'] = df.apply(determine_phase, axis=1)
    
    ordered_cols = [
        'Timestamp',
        'event_type',
        'event_value',
        'LIT101',
        'FIT101',
        'P101_state',
        'P102_state',
        'MV101_state',
        'level_change',
        'level_slope',
        'level_slope_300s',
        'robust_level_slope',
        'level_trend_direction',
        'command_frequency',
        'command_frequency_60s',
        'p101_frequency',
        'p102_frequency',
        'mv101_frequency',
        'command_repetition',
        'consecutive_same_command',
        'command_value_change',
        'command_value_change_desc',
        'process_phase',
        'Normal/Attack'
    ]
    
    return df[ordered_cols]

def compare_features_normal_vs_attack(df):
    """
    Calculate and compare feature statistics separately for Normal and Attack data.
    """
    df_norm = df[df['Normal/Attack'] == 'Normal']
    df_att = df[df['Normal/Attack'] == 'Attack']
    
    num_cols = [
        'command_frequency',
        'command_frequency_60s',
        'command_repetition',
        'consecutive_same_command',
        'command_value_change',
        'level_change',
        'level_slope',
        'level_slope_300s',
        'robust_level_slope'
    ]
    
    stats_list = []
    percentiles = [25, 50, 75, 90, 95, 99]
    
    for col in num_cols:
        # Normal stats
        n_vals = df_norm[col].dropna()
        n_mean, n_std, n_median = n_vals.mean(), n_vals.std(), n_vals.median()
        n_min, n_max = n_vals.min(), n_vals.max()
        n_pcts = {f"p{p}": np.percentile(n_vals, p) for p in percentiles}
        
        # Attack stats
        a_vals = df_att[col].dropna()
        a_mean, a_std, a_median = a_vals.mean(), a_vals.std(), a_vals.median()
        a_min, a_max = a_vals.min(), a_vals.max()
        a_pcts = {f"p{p}": np.percentile(a_vals, p) for p in percentiles}
        
        row_n = {
            'Feature': col, 'Dataset': 'Normal',
            'Mean': n_mean, 'Median': n_median, 'Std': n_std,
            'Min': n_min, 'Max': n_max,
            'P25': n_pcts['p25'], 'P75': n_pcts['p75'], 'P90': n_pcts['p90'], 'P95': n_pcts['p95'], 'P99': n_pcts['p99']
        }
        row_a = {
            'Feature': col, 'Dataset': 'Attack',
            'Mean': a_mean, 'Median': a_median, 'Std': a_std,
            'Min': a_min, 'Max': a_max,
            'P25': a_pcts['p25'], 'P75': a_pcts['p75'], 'P90': a_pcts['p90'], 'P95': a_pcts['p95'], 'P99': a_pcts['p99']
        }
        stats_list.append(row_n)
        stats_list.append(row_a)
        
    df_stats = pd.DataFrame(stats_list)
    return df_stats

def generate_context_plots(df):
    """
    Generate 6 comprehensive visual plots for context feature analysis.
    """
    os.makedirs(PLOTS_DIR, exist_ok=True)
    
    # Separate Normal and Attack subsets for plotting
    norm_mask = df['Normal/Attack'] == 'Normal'
    att_mask = df['Normal/Attack'] == 'Attack'
    
    # 1. Command Frequency Over Time
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(df['Timestamp'], df['command_frequency'], color='navy', linewidth=0.8, label='Command Frequency (300s window)')
    ax.plot(df['Timestamp'], df['command_frequency_60s'], color='crimson', linewidth=0.8, alpha=0.7, label='Command Frequency (60s window)')
    ax.set_title('Context Feature: Command Frequency Over Time')
    ax.set_ylabel('Events / Window')
    ax.set_xlabel('Timestamp')
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "command_frequency_over_time.png"), dpi=150)
    plt.close()
    
    # 2. Command Repetition Over Time
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(df['Timestamp'], df['command_repetition'], color='teal', linewidth=0.8, label='Max Command Repetition (300s window)')
    ax.set_title('Context Feature: Command Repetition Over Time')
    ax.set_ylabel('Repetitions / Window')
    ax.set_xlabel('Timestamp')
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "command_repetition_over_time.png"), dpi=150)
    plt.close()
    
    # 3. Command Value Changes Over Time
    fig, ax = plt.subplots(figsize=(12, 5))
    events_df = df[df['command_value_change'] > 0]
    ax.scatter(events_df['Timestamp'], events_df['command_value_change'], c=events_df['command_value_change'], cmap='coolwarm', s=15, zorder=5)
    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(['None (0)', 'Single Event (1)', 'Compound Event (2)'])
    ax.set_title('Context Feature: Command Value Changes (Event Severity)')
    ax.set_xlabel('Timestamp')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "command_value_changes.png"), dpi=150)
    plt.close()
    
    # 4. Tank Level & Trends (Raw vs Robust Slope)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax1.plot(df['Timestamp'], df['LIT101'], color='blue', linewidth=0.8, label='LIT101 Level (mm)')
    ax1.set_title('Tank Level (LIT101)')
    ax1.set_ylabel('Level (mm)')
    ax1.legend(loc='upper right')
    ax1.grid(True, alpha=0.3)
    
    ax2.plot(df['Timestamp'], df['level_slope'], color='orange', linewidth=0.5, alpha=0.5, label='Raw 1s Slope (mm/s)')
    ax2.plot(df['Timestamp'], df['robust_level_slope'], color='darkred', linewidth=1.0, label='Robust Rolling Slope (300s median)')
    ax2.set_title('Context Feature: Tank Level Trend Slopes')
    ax2.set_ylabel('Slope (mm/s)')
    ax2.set_xlabel('Timestamp')
    ax2.legend(loc='upper right')
    ax2.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "level_and_trends.png"), dpi=150)
    plt.close()
    
    # 5. Process Phase Distribution (Normal vs Attack)
    phase_dist = df.groupby(['process_phase', 'Normal/Attack']).size().unstack(fill_value=0)
    phase_dist_pct = phase_dist.div(phase_dist.sum(axis=0), axis=1) * 100
    
    fig, ax = plt.subplots(figsize=(10, 5))
    phase_dist_pct.plot(kind='bar', ax=ax, color=['navy', 'crimson'])
    ax.set_title('Context Feature: Process Phase Distribution (% of Time)')
    ax.set_ylabel('Percentage (%)')
    ax.set_xlabel('Process Phase')
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "process_phase_distribution.png"), dpi=150)
    plt.close()
    
    # 6. Normal vs Attack Feature Distribution Comparison (Boxplots)
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    
    df.boxplot(column='command_frequency', by='Normal/Attack', ax=axes[0,0])
    axes[0,0].set_title('Command Frequency (300s window)')
    
    df.boxplot(column='command_repetition', by='Normal/Attack', ax=axes[0,1])
    axes[0,1].set_title('Command Repetition (300s window)')
    
    df.boxplot(column='robust_level_slope', by='Normal/Attack', ax=axes[1,0])
    axes[1,0].set_title('Robust Level Slope (mm/s)')
    
    df.boxplot(column='level_slope_300s', by='Normal/Attack', ax=axes[1,1])
    axes[1,1].set_title('300s Level Slope (mm/s)')
    
    plt.suptitle('Normal vs Attack Context Feature Comparison', fontsize=14)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "normal_vs_attack_feature_comparison.png"), dpi=150)
    plt.close()
    
    print(f"Saved 6 context feature plots under: {PLOTS_DIR}")

def main():
    print(">>> 1. Loading Stage 1 events dataset ...")
    df_events = pd.read_csv(INPUT_EVENTS)
    
    print(">>> 2. Engineering Command-Context Features ...")
    df_features = build_context_features(df_events)
    
    print(">>> 3. Saving feature dataset ...")
    df_features.to_csv(OUTPUT_FEATURES, index=False)
    print(f"Saved: {OUTPUT_FEATURES} ({len(df_features)} rows)")
    
    print(">>> 4. Comparing NORMAL vs ATTACK Feature Statistics ...")
    df_stats = compare_features_normal_vs_attack(df_features)
    
    print("\n==========================================================================")
    print("           NORMAL vs ATTACK FEATURE COMPARISON SUMMARY")
    print("==========================================================================")
    print(df_stats[['Feature', 'Dataset', 'Mean', 'Median', 'Std', 'Min', 'Max', 'P90', 'P95', 'P99']].to_string(index=False))
    
    print("\nProcess Phase Distribution (Normal vs Attack):")
    phase_summary = pd.crosstab(df_features['process_phase'], df_features['Normal/Attack'], normalize='columns') * 100
    print(phase_summary.round(2).to_string())
    
    print("\n>>> 5. Generating Context Feature Plots ...")
    generate_context_plots(df_features)
    
    print("\n>>> 6. Validation Suite ...")
    print(f"Total Rows: {len(df_features)}")
    print(f"Missing Values Count: {df_features.isnull().sum().sum()}")
    print(f"Timestamp Duplicate Count: {df_features['Timestamp'].duplicated().sum()}")
    print(f"Timestamp Monotonic Order: {pd.to_datetime(df_features['Timestamp']).is_monotonic_increasing}")
    print(f"Initial Window Rows Handling: bfill/min_periods=1 used (0 rows dropped).")

if __name__ == "__main__":
    main()
