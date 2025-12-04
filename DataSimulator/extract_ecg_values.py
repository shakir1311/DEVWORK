#!/usr/bin/env python3
"""
Extract raw ECG values from the first patient's .mat file.
PURE raw values - no .hea file, no conversion, no modification.
"""

import os
import sys
import numpy as np
import scipy.io
from ecg_simulator import ECGSimulator

def extract_first_patient_ecg():
    """Extract and save ECG values from the first patient - RAW from .mat only."""
    
    # Initialize simulator to get patient list
    simulator = ECGSimulator()
    
    if not simulator.check_dataset_available():
        print("ERROR: Dataset not found. Please download the dataset first.")
        return
    
    # Get first patient
    patients = simulator.list_patients()
    if not patients:
        print("ERROR: No patients found in dataset.")
        return
    
    first_patient = patients[0]
    print(f"Extracting RAW ECG values for first patient: {first_patient}")
    
    # Load ECG data - ONLY from .mat file, NO .hea file
    mat_file = os.path.join(simulator.TRAINING_DIR, f"{first_patient}.mat")
    
    if not os.path.exists(mat_file):
        print(f"ERROR: .mat file not found: {mat_file}")
        print("Please ensure the dataset is downloaded.")
        return
    
    # Load raw ECG data from .mat file - NO CONVERSION, NO MODIFICATION, NO .HEA
    print(f"Loading .mat file: {mat_file}")
    mat_data = scipy.io.loadmat(mat_file)
    ecg_raw = mat_data['val'][0]  # Raw values directly from .mat file
    
    print(f"Raw samples from .mat: {len(ecg_raw)}")
    print(f"Data type: {ecg_raw.dtype}")
    
    # Use raw values directly - NO conversion, NO modification, NO .hea info
    ecg_final = ecg_raw.astype(np.float64)  # Keep as float64 for precision
    
    print(f"\nRaw ECG values (directly from .mat file, no .hea, no conversion):")
    print(f"  Total samples: {len(ecg_final)}")
    print(f"  Min value: {np.min(ecg_final):.6f}")
    print(f"  Max value: {np.max(ecg_final):.6f}")
    print(f"  Mean value: {np.mean(ecg_final):.6f}")
    print(f"  Std deviation: {np.std(ecg_final):.6f}")
    
    # Save to CSV file for easy comparison (sanitize filename - replace / with _)
    safe_patient_id = first_patient.replace('/', '_')
    output_file = f"{safe_patient_id}_ecg_values.csv"
    print(f"\nSaving all values to: {output_file}")
    
    with open(output_file, 'w') as f:
        f.write("Sample_Index,Raw_ECG_Value\n")
        for i, value in enumerate(ecg_final):
            f.write(f"{i},{value:.6f}\n")
    
    print(f"✓ Saved {len(ecg_final)} samples to {output_file}")
    
    # Also print first 100 values for quick reference
    print(f"\nFirst 100 raw ECG values:")
    print("Sample | Raw Value")
    print("-" * 25)
    for i in range(min(100, len(ecg_final))):
        print(f"{i:6d} | {ecg_final[i]:10.6f}")
    
    if len(ecg_final) > 100:
        print(f"... ({len(ecg_final) - 100} more samples)")
    
    print(f"\n✓ Extraction complete!")
    print(f"  Patient ID: {first_patient}")
    print(f"  Output file: {output_file}")
    print(f"  Format: CSV with columns: Sample_Index, Raw_ECG_Value")
    print(f"  Note: Values are PURE RAW from .mat file - NO .hea, NO conversion, NO modification")

if __name__ == "__main__":
    try:
        extract_first_patient_ecg()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

