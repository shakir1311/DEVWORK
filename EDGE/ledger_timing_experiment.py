#!/usr/bin/env python3
"""
Ledger Performance Timing Experiment

Measures DB insertion time with Ledger ON vs OFF using real ECG data
and inference results from the bulk experiment.

Usage:
    python ledger_timing_experiment.py --ledger-on   # Run with ledger enabled
    python ledger_timing_experiment.py --ledger-off  # Run with ledger disabled
"""

import sys
import json
import time
import argparse
import datetime as dt
from pathlib import Path
import scipy.io as sio
import numpy as np

# Add project paths
EDGE_DIR = Path(__file__).parent
sys.path.insert(0, str(EDGE_DIR))

PORTAL_DIR = EDGE_DIR.parent / 'Web'
sys.path.insert(0, str(PORTAL_DIR))

# Paths
RESULTS_FILE = EDGE_DIR / 'experiment_results/ledger_on_xai_off_20251220_203805/results.jsonl'
DATASET_DIR = EDGE_DIR.parent / 'DataSimulator/data/cinc2017/training'
OUTPUT_DIR = EDGE_DIR / 'experiment_results/ledger_timing'


def setup_db():
    """Connect to Portal's SQLite database."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    
    db_path = PORTAL_DIR / 'portal.db'
    engine = create_engine(
        f'sqlite:///{db_path}',
        echo=False,
        connect_args={"timeout": 30}
    )
    
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    import models
    import ledger
    
    return SessionLocal(), models, ledger


def load_ecg(patient_id: str) -> list:
    """Load ECG data from .mat file."""
    mat_file = DATASET_DIR / f"{patient_id}.mat"
    if not mat_file.exists():
        return []
    mat = sio.loadmat(str(mat_file))
    return mat['val'].flatten().astype(float).tolist()


def insert_record(db, models, ledger_mod, record: dict, ecg_data: list, ledger_enabled: bool) -> float:
    """Insert a single record and return insertion time in ms."""
    start = time.perf_counter()
    
    try:
        # Find or create patient
        patient = db.query(models.Patient).filter(
            models.Patient.patient_id_external == record['patient_id']
        ).first()
        
        if not patient:
            patient = models.Patient(
                patient_id_external=record['patient_id'],
                name=f"Patient {record['patient_id']}",
                dob="Unknown"
            )
            db.add(patient)
            db.flush()
        
        # Create ECG record
        new_record = models.ECGRecord(
            patient_id=patient.id,
            timestamp=dt.datetime.utcnow(),
            device_id="LEDGER_TIMING_TEST",
            heart_rate=0.0,  # Not relevant for timing test
            classification=record['predicted_class'],
            confidence=record['confidence'],
            ecg_data=ecg_data,
            processing_results={"test": "ledger_timing"}
        )
        db.add(new_record)
        db.flush()
        
        # Add ledger entry if enabled
        if ledger_enabled:
            ledger_mod.add_audit_entry(
                db,
                actor_id="LEDGER_TIMING_TEST",
                action="INGEST_ECG",
                details={"record_id": new_record.id, "patient": record['patient_id']},
                auto_commit=False
            )
        
        db.commit()
        
    except Exception as e:
        print(f"Error inserting {record['patient_id']}: {e}")
        db.rollback()
    
    return (time.perf_counter() - start) * 1000  # ms


def run_experiment(ledger_enabled: bool, limit: int = None):
    """Run the timing experiment."""
    mode = "LEDGER_ON" if ledger_enabled else "LEDGER_OFF"
    print(f"\n{'='*60}")
    print(f"Starting {mode} Experiment")
    print(f"{'='*60}\n")
    
    # Load results
    with open(RESULTS_FILE, 'r') as f:
        results = [json.loads(line) for line in f if line.strip()]
    
    if limit:
        results = results[:limit]
    
    print(f"Loaded {len(results)} records to insert")
    
    # Setup DB
    db, models, ledger_mod = setup_db()
    
    # Timing data
    insert_times = []
    
    start_total = time.perf_counter()
    
    for i, record in enumerate(results):
        if not record['success']:
            continue
        
        # Load ECG data
        ecg_data = load_ecg(record['patient_id'])
        if not ecg_data:
            continue
        
        # Insert with timing
        insert_time = insert_record(db, models, ledger_mod, record, ecg_data, ledger_enabled)
        insert_times.append(insert_time)
        
        # Progress
        if (i + 1) % 500 == 0:
            avg_so_far = np.mean(insert_times)
            print(f"  Processed {i+1}/{len(results)} - Avg: {avg_so_far:.2f}ms/record")
    
    total_time = (time.perf_counter() - start_total) * 1000
    
    # Close DB
    db.close()
    
    # Calculate stats
    stats = {
        "mode": mode,
        "ledger_enabled": ledger_enabled,
        "total_records": len(insert_times),
        "total_time_ms": total_time,
        "avg_insert_time_ms": np.mean(insert_times),
        "median_insert_time_ms": np.median(insert_times),
        "min_insert_time_ms": np.min(insert_times),
        "max_insert_time_ms": np.max(insert_times),
        "std_insert_time_ms": np.std(insert_times),
        "records_per_second": len(insert_times) / (total_time / 1000)
    }
    
    # Save results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"{mode.lower()}_results.json"
    with open(output_file, 'w') as f:
        json.dump(stats, f, indent=2)
    
    # Also save raw timing data
    timing_file = OUTPUT_DIR / f"{mode.lower()}_timings.json"
    with open(timing_file, 'w') as f:
        json.dump(insert_times, f)
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"{mode} Results:")
    print(f"{'='*60}")
    print(f"  Total Records:    {stats['total_records']}")
    print(f"  Total Time:       {stats['total_time_ms']/1000:.2f}s")
    print(f"  Avg Insert Time:  {stats['avg_insert_time_ms']:.3f}ms")
    print(f"  Median:           {stats['median_insert_time_ms']:.3f}ms")
    print(f"  Min/Max:          {stats['min_insert_time_ms']:.3f}ms / {stats['max_insert_time_ms']:.3f}ms")
    print(f"  Records/Second:   {stats['records_per_second']:.1f}")
    print(f"\nResults saved to: {output_file}")
    
    return stats


def main():
    parser = argparse.ArgumentParser(description='Ledger Performance Timing Experiment')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--ledger-on', action='store_true', help='Run with ledger enabled')
    group.add_argument('--ledger-off', action='store_true', help='Run with ledger disabled')
    parser.add_argument('--limit', type=int, help='Limit number of records (for testing)')
    
    args = parser.parse_args()
    
    ledger_enabled = args.ledger_on
    run_experiment(ledger_enabled, limit=args.limit)


if __name__ == '__main__':
    main()
