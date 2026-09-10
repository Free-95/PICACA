import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

PROJECT_DIR = r"e:\ISA_PROJECT"
INPUT_CLEAN = os.path.join(PROJECT_DIR, "processed", "swat_stage1_clean.csv")
OUTPUT_EVENTS = os.path.join(PROJECT_DIR, "processed", "swat_stage1_events.csv")
PLOTS_DIR = os.path.join(PROJECT_DIR, "plots")

def extract_events_and_dynamics(df):
    """
    Extract discrete actuator command events and physical process dynamics.
    """
    df = df.copy()
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    
    # Calculate timestamp interval in seconds
    time_delta_sec = df['Timestamp'].diff().dt.total_seconds().fillna(1.0)
    time_delta_sec = time_delta_sec.replace(0, 1.0)
    
    # 1. Process Dynamics Calculations
    df['level_change'] = df['LIT101'].diff().fillna(0.0)
    df['level_slope'] = (df['level_change'] / time_delta_sec).fillna(0.0)
    
    df['flow_change'] = df['FIT101'].diff().fillna(0.0)
    df['flow_slope'] = (df['flow_change'] / time_delta_sec).fillna(0.0)
    
    # 2. Discrete Actuator Event Extraction
    # P101 transitions (0 -> 1: P101_ON, 1 -> 0: P101_OFF)
    p101_diff = df['P101_state'].diff()
    
    # P102 transitions (0 -> 1: P102_ON, 1 -> 0: P102_OFF)
    p102_diff = df['P102_state'].diff()
    
    # MV101 transitions (state change)
    mv101_prev = df['MV101_state'].shift(1)
    mv101_changed = (df['MV101_state'] != mv101_prev)
    mv101_changed.iloc[0] = False
    
    event_types = []
    event_values = []
    
    for i in range(len(df)):
        ev_t = []
        ev_v = []
        
        # Check P101
        if p101_diff.iloc[i] == 1:
            ev_t.append('P101_ON')
            ev_v.append('1')
        elif p101_diff.iloc[i] == -1:
            ev_t.append('P101_OFF')
            ev_v.append('0')
            
        # Check P102
        if p102_diff.iloc[i] == 1:
            ev_t.append('P102_ON')
            ev_v.append('1')
        elif p102_diff.iloc[i] == -1:
            ev_t.append('P102_OFF')
            ev_v.append('0')
            
        # Check MV101
        if mv101_changed.iloc[i]:
            ev_t.append('MV101_CHANGE')
            ev_v.append(str(df['MV101_state'].iloc[i]))
            
        if ev_t:
            event_types.append(";".join(ev_t))
            event_values.append(";".join(ev_v))
        else:
            event_types.append('NONE')
            event_values.append('NONE')
            
    df['event_type'] = event_types
    df['event_value'] = event_values
    
    # Reorder columns as requested
    ordered_cols = [
        'Timestamp',
        'LIT101',
        'FIT101',
        'MV101',
        'MV101_state',
        'P101',
        'P101_state',
        'P102',
        'P102_state',
        'Normal/Attack',
        'event_type',
        'event_value',
        'level_change',
        'level_slope',
        'flow_change',
        'flow_slope'
    ]
    df_events = df[ordered_cols]
    return df_events

def generate_validation_plots(df):
    """
    Generate plots showing LIT101, FIT101, Actuator states, and marked events.
    """
    os.makedirs(PLOTS_DIR, exist_ok=True)
    plot_path = os.path.join(PLOTS_DIR, "stage1_events_overview.png")
    
    fig, axes = plt.subplots(4, 1, figsize=(15, 12), sharex=True)
    
    # Plot 1: LIT101 vs Time
    axes[0].plot(df['Timestamp'], df['LIT101'], color='blue', linewidth=0.8, label='LIT101 Tank Level (mm)')
    axes[0].axhline(y=800, color='red', linestyle='--', alpha=0.5, label='High Limit (800mm)')
    axes[0].axhline(y=200, color='orange', linestyle='--', alpha=0.5, label='Low Limit (200mm)')
    axes[0].set_ylabel('Level (mm)')
    axes[0].set_title('SWaT Stage 1: Tank Level (LIT101)')
    axes[0].legend(loc='upper right')
    axes[0].grid(True, alpha=0.3)
    
    # Plot 2: FIT101 vs Time
    axes[1].plot(df['Timestamp'], df['FIT101'], color='cyan', linewidth=0.8, label='FIT101 Inflow (m3/h)')
    axes[1].set_ylabel('Flow (m3/h)')
    axes[1].set_title('SWaT Stage 1: Flow Rate (FIT101)')
    axes[1].legend(loc='upper right')
    axes[1].grid(True, alpha=0.3)
    
    # Plot 3: Pumps P101 / P102 states
    axes[2].plot(df['Timestamp'], df['P101_state'], color='green', linewidth=1.0, label='P101 State (0=OFF, 1=ON)')
    axes[2].plot(df['Timestamp'], df['P102_state'] + 0.05, color='darkgreen', linewidth=1.0, linestyle=':', label='P102 State (0=OFF, 1=ON)')
    axes[2].set_ylabel('Pump State')
    axes[2].set_yticks([0, 1])
    axes[2].set_title('SWaT Stage 1: Pump States (P101 & P102)')
    axes[2].legend(loc='upper right')
    axes[2].grid(True, alpha=0.3)
    
    # Plot 4: Valve MV101 state & Event Markers
    mv_numeric = df['MV101_state'].map({'Closed': 0, 'Transition': 0.5, 'Open': 1.0})
    axes[3].plot(df['Timestamp'], mv_numeric, color='purple', linewidth=1.0, label='MV101 (0=Closed, 0.5=Trans, 1=Open)')
    
    # Overlay discrete events
    events_df = df[df['event_type'] != 'NONE']
    axes[3].scatter(events_df['Timestamp'], [1.1]*len(events_df), color='red', s=10, zorder=5, label='Actuator Events')
    
    axes[3].set_ylabel('Valve State')
    axes[3].set_yticks([0, 0.5, 1.0])
    axes[3].set_yticklabels(['Closed', 'Trans', 'Open'])
    axes[3].set_title('SWaT Stage 1: Valve State (MV101) & Actuator Event Occurrences')
    axes[3].set_xlabel('Timestamp')
    axes[3].legend(loc='upper right')
    axes[3].grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(plot_path, dpi=150)
    plt.close()
    print(f"Saved validation plot: {plot_path}")

def main():
    print(">>> 1. Loading cleaned Stage 1 dataset ...")
    df_clean = pd.read_csv(INPUT_CLEAN)
    
    print(">>> 2. Extracting discrete actuator events and physical dynamics ...")
    df_events = extract_events_and_dynamics(df_clean)
    
    print(">>> 3. Saving event dataset ...")
    df_events.to_csv(OUTPUT_EVENTS, index=False)
    print(f"Saved: {OUTPUT_EVENTS} ({len(df_events)} rows)")
    
    print(">>> 4. Generating validation plots ...")
    generate_validation_plots(df_events)
    
    print("\n>>> 5. Validation Suite ...")
    print(f"Total Rows: {len(df_events)}")
    print(f"Missing Values: {df_events.isnull().sum().to_dict()}")
    print(f"Timestamp Duplicate Count: {df_events['Timestamp'].duplicated().sum()}")
    print(f"Timestamp Monotonic Increasing: {df_events['Timestamp'].is_monotonic_increasing}")
    
    print("\nEvent Type Distribution:")
    print(df_events['event_type'].value_counts())
    
    print("\nNormal/Attack Distribution:")
    print(df_events['Normal/Attack'].value_counts())
    
    print(f"\nLevel Change (mm) Min/Max: [{df_events['level_change'].min():.4f}, {df_events['level_change'].max():.4f}]")
    print(f"Level Slope (mm/s) Min/Max: [{df_events['level_slope'].min():.4f}, {df_events['level_slope'].max():.4f}]")
    print(f"Flow Change (m3/h) Min/Max: [{df_events['flow_change'].min():.4f}, {df_events['flow_change'].max():.4f}]")
    
    event_indices = df_events[df_events['event_type'] != 'NONE'].index.tolist()
    print(f"\nTotal Event Timestamps: {len(event_indices)}")
    
    print("\n--- SAMPLE EVENT TRANSITION 1 (P101 Event) ---")
    p101_sample_idx = df_events[df_events['event_type'].str.contains('P101')].index[0]
    print(df_events.iloc[max(0, p101_sample_idx-3):p101_sample_idx+4][
        ['Timestamp', 'LIT101', 'P101_state', 'MV101_state', 'event_type', 'event_value', 'Normal/Attack']
    ].to_string())

    print("\n--- SAMPLE EVENT TRANSITION 2 (MV101 Event) ---")
    mv101_sample_idx = df_events[df_events['event_type'].str.contains('MV101')].index[0]
    print(df_events.iloc[max(0, mv101_sample_idx-3):mv101_sample_idx+4][
        ['Timestamp', 'LIT101', 'P101_state', 'MV101_state', 'event_type', 'event_value', 'Normal/Attack']
    ].to_string())

if __name__ == "__main__":
    main()
