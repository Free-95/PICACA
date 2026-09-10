import os
import pandas as pd
import numpy as np

PROJECT_DIR = r"e:\ISA_PROJECT"
PROCESSED_DIR = os.path.join(PROJECT_DIR, "processed")
NORMAL_RAW = os.path.join(PROJECT_DIR, "normal.csv")
ATTACK_RAW = os.path.join(PROJECT_DIR, "attack.csv")
MERGED_RAW = os.path.join(PROJECT_DIR, "merged.csv")

def map_actuator_states(df):
    """
    Preserve original actuator values and create derived normalized state columns.
    P101 / P102:
        1 -> 0 (OFF)
        2 -> 1 (ON)
    MV101:
        1 -> 'Closed'
        2 -> 'Open'
        0 -> 'Transition'
    """
    # P101 state (0 = OFF, 1 = ON)
    df['P101_state'] = df['P101'].map({1: 0, 2: 1})
    
    # P102 state (0 = OFF, 1 = ON)
    df['P102_state'] = df['P102'].map({1: 0, 2: 1})
    
    # MV101 state
    mv_map = {1: 'Closed', 2: 'Open', 0: 'Transition', 1.0: 'Closed', 2.0: 'Open', 0.0: 'Transition'}
    df['MV101_state'] = df['MV101'].map(mv_map)
    
    return df

def clean_dataframe(filepath):
    """
    Load CSV, strip whitespace from columns and string values,
    parse timestamps, and extract Stage 1 signals.
    """
    df = pd.read_csv(filepath)
    
    # 1. Strip whitespace from column names
    df.columns = [c.strip() for c in df.columns]
    
    # 2. Strip whitespace from string columns
    for col in df.select_dtypes(include=['object', 'string']).columns:
        df[col] = df[col].astype(str).str.strip()
        
    # 3. Parse Timestamp into datetime object
    df['Timestamp'] = pd.to_datetime(df['Timestamp'], format='%d/%m/%Y %I:%M:%S %p')
    
    # 4. Select Stage 1 fields
    stage1_cols = ['Timestamp', 'LIT101', 'FIT101', 'MV101', 'P101', 'P102', 'Normal/Attack']
    df_stage1 = df[stage1_cols].copy()
    
    # 5. Map normalized actuator states
    df_stage1 = map_actuator_states(df_stage1)
    
    # Reorder columns logically
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
        'Normal/Attack'
    ]
    df_stage1 = df_stage1[ordered_cols]
    
    return df_stage1

def main():
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    
    # Save raw file sizes and mtimes for immutability verification
    raw_files = [NORMAL_RAW, ATTACK_RAW, MERGED_RAW]
    raw_mtimes_before = {f: os.path.getmtime(f) for f in raw_files if os.path.exists(f)}
    
    print(">>> 1. Processing Normal Baseline Dataset ...")
    df_normal_all = clean_dataframe(NORMAL_RAW)
    
    # Filter Block 1: Clean contiguous normal baseline using actual timestamp criteria
    # Start: 2015-12-28 10:00:00, End: 2016-01-02 14:59:59
    start_ts = pd.to_datetime('2015-12-28 10:00:00')
    end_ts = pd.to_datetime('2016-01-02 14:59:59')
    
    normal_mask = (
        (df_normal_all['Timestamp'] >= start_ts) & 
        (df_normal_all['Timestamp'] <= end_ts) & 
        (~df_normal_all['MV101'].isnull())
    )
    df_normal_clean = df_normal_all[normal_mask].copy().reset_index(drop=True)
    
    # Save processed normal dataset
    normal_out_path = os.path.join(PROCESSED_DIR, "swat_stage1_normal.csv")
    df_normal_clean.to_csv(normal_out_path, index=False)
    print(f"Saved: {normal_out_path} ({len(df_normal_clean)} rows)")
    
    print("\n>>> 2. Processing Attack Dataset ...")
    df_attack_clean = clean_dataframe(ATTACK_RAW)
    
    attack_out_path = os.path.join(PROCESSED_DIR, "swat_stage1_attack.csv")
    df_attack_clean.to_csv(attack_out_path, index=False)
    print(f"Saved: {attack_out_path} ({len(df_attack_clean)} rows)")
    
    print("\n>>> 3. Creating Combined Stage 1 Dataset ...")
    df_combined = pd.concat([df_normal_clean, df_attack_clean], ignore_index=True)
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)
    
    combined_out_path = os.path.join(PROCESSED_DIR, "swat_stage1_clean.csv")
    df_combined.to_csv(combined_out_path, index=False)
    print(f"Saved: {combined_out_path} ({len(df_combined)} rows)")
    
    print("\n>>> 4. Validation Suite ...")
    # Verify raw files untampered
    raw_mtimes_after = {f: os.path.getmtime(f) for f in raw_files if os.path.exists(f)}
    for f in raw_files:
        assert raw_mtimes_before[f] == raw_mtimes_after[f], f"RAW file {f} was modified!"
    print("Pass: All raw files remain 100% untouched.")
    
    # Run detailed checks on datasets
    datasets = {
        'swat_stage1_normal.csv': df_normal_clean,
        'swat_stage1_attack.csv': df_attack_clean,
        'swat_stage1_clean.csv': df_combined
    }
    
    for name, df_curr in datasets.items():
        print(f"\n==========================================")
        print(f" Validation Report: {name}")
        print(f"==========================================")
        print(f"Shape: {df_curr.shape} (rows={len(df_curr)}, cols={len(df_curr.columns)})")
        print(f"Columns: {list(df_curr.columns)}")
        print(f"Missing Values: {df_curr.isnull().sum().to_dict()}")
        
        dup_ts = df_curr['Timestamp'].duplicated().sum()
        is_sorted = df_curr['Timestamp'].is_monotonic_increasing
        ts_diffs = df_curr['Timestamp'].diff().dropna()
        sec_diffs = ts_diffs.dt.total_seconds().unique()
        print(f"Duplicate Timestamps: {dup_ts}")
        print(f"Chronologically Sorted: {is_sorted}")
        print(f"Timestamp Time Step Intervals (sec): {sec_diffs[:10]} (unique total={len(sec_diffs)})")
        print(f"Time Range: {df_curr['Timestamp'].min()} to {df_curr['Timestamp'].max()}")
        
        print(f"P101 original unique: {df_curr['P101'].unique().tolist()} | P101_state unique: {df_curr['P101_state'].unique().tolist()}")
        print(f"P102 original unique: {df_curr['P102'].unique().tolist()} | P102_state unique: {df_curr['P102_state'].unique().tolist()}")
        print(f"MV101 original unique: {df_curr['MV101'].unique().tolist()} | MV101_state unique: {df_curr['MV101_state'].unique().tolist()}")
        
        print(f"LIT101 Min/Max: [{df_curr['LIT101'].min():.2f}, {df_curr['LIT101'].max():.2f}]")
        print(f"FIT101 Min/Max: [{df_curr['FIT101'].min():.2f}, {df_curr['FIT101'].max():.2f}]")
        
        print("Normal/Attack Distribution:")
        print(df_curr['Normal/Attack'].value_counts().to_dict())

if __name__ == "__main__":
    main()
