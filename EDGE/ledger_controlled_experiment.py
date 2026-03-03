#!/usr/bin/env python3
"""
Controlled Ledger Performance Experiment

Runs BOTH experiments in a single script with identical conditions:
1. Clear DB completely (all tables)
2. Run Ledger OFF experiment
3. Clear DB completely again
4. Run Ledger ON experiment
5. Compare results

This ensures fair comparison with identical:
- System state
- SQLite caching
- Python interpreter state
- File system caching
"""

import sys
import json
import time
import gc
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
OUTPUT_DIR = EDGE_DIR / 'experiment_results/ledger_timing_controlled'


def get_system_stats():
    """Get current system resource usage."""
    import subprocess
    
    # Get CPU usage
    cpu_result = subprocess.run(
        ["ps", "-A", "-o", "%cpu"],
        capture_output=True, text=True
    )
    cpu_lines = cpu_result.stdout.strip().split('\n')[1:]  # Skip header
    total_cpu = sum(float(x) for x in cpu_lines if x.strip())
    
    # Get memory info
    mem_result = subprocess.run(
        ["vm_stat"],
        capture_output=True, text=True
    )
    
    # Parse vm_stat output
    mem_lines = mem_result.stdout.strip().split('\n')
    page_size = 16384  # macOS default page size
    free_pages = 0
    active_pages = 0
    
    for line in mem_lines:
        if 'free:' in line:
            free_pages = int(line.split(':')[1].strip().rstrip('.'))
        elif 'active:' in line:
            active_pages = int(line.split(':')[1].strip().rstrip('.'))
    
    free_mb = (free_pages * page_size) / (1024 * 1024)
    active_mb = (active_pages * page_size) / (1024 * 1024)
    
    return {
        "cpu_usage_total": round(total_cpu, 1),
        "ram_free_mb": round(free_mb, 0),
        "ram_active_mb": round(active_mb, 0)
    }


def wait_for_stable_system(target_cpu=50, wait_time=5, prev_ram=None):
    """Wait for system to reach stable state before experiment."""
    print(f"  Waiting for system to stabilize (target CPU < {target_cpu}%)...")
    
    for attempt in range(15):  # Max 15 attempts (75 seconds)
        stats = get_system_stats()
        cpu_ok = stats['cpu_usage_total'] < target_cpu
        
        # If we have previous RAM measurement, wait for RAM to be within 20% of it
        if prev_ram is not None:
            ram_diff_pct = abs(stats['ram_free_mb'] - prev_ram) / prev_ram * 100
            ram_ok = ram_diff_pct < 30  # Within 30%
        else:
            ram_ok = True
        
        if cpu_ok and ram_ok:
            print(f"  System stable: CPU={stats['cpu_usage_total']:.1f}%, RAM Free={stats['ram_free_mb']:.0f}MB")
            return stats
        
        if not cpu_ok:
            print(f"    Waiting... CPU={stats['cpu_usage_total']:.1f}% (target <{target_cpu}%)")
        if prev_ram and not ram_ok:
            print(f"    Waiting... RAM={stats['ram_free_mb']:.0f}MB (target ~{prev_ram:.0f}MB)")
        
        time.sleep(wait_time)
    
    stats = get_system_stats()
    print(f"  Proceeding: CPU={stats['cpu_usage_total']:.1f}%, RAM Free={stats['ram_free_mb']:.0f}MB")
    return stats


def clear_database():
    """Completely clear all database tables."""
    from database import SessionLocal, engine, Base
    import models
    
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # Clear in order respecting foreign keys using ORM
        db.query(models.ECGRecord).delete()
        db.query(models.Patient).delete()
        
        # Clear audit_ledger using text() for raw SQL
        from sqlalchemy import text
        db.execute(text("DELETE FROM audit_ledger"))
        
        db.commit()
    except Exception as e:
        print(f"Warning during clear: {e}")
        db.rollback()
    finally:
        db.close()
    
    # Force garbage collection
    gc.collect()
    
    # Small delay to ensure SQLite syncs
    time.sleep(0.5)


def setup_fresh_db():
    """Get a fresh database connection."""
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


def run_single_experiment(records: list, ledger_enabled: bool, limit: int = None) -> dict:
    """Run a single experiment and return timing statistics."""
    mode = "LEDGER_ON" if ledger_enabled else "LEDGER_OFF"
    
    if limit:
        records = records[:limit]
    
    # Fresh DB connection
    db, models, ledger_mod = setup_fresh_db()
    
    insert_times = []
    start_total = time.perf_counter()
    
    for i, record in enumerate(records):
        if not record['success']:
            continue
        
        ecg_data = load_ecg(record['patient_id'])
        if not ecg_data:
            continue
        
        # Time the insertion
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
                timestamp=dt.datetime.now(dt.UTC),
                device_id="CONTROLLED_TEST",
                heart_rate=0.0,
                classification=record['predicted_class'],
                confidence=record['confidence'],
                ecg_data=ecg_data,
                processing_results={"test": mode}
            )
            db.add(new_record)
            db.flush()
            
            # Add ledger entry if enabled
            if ledger_enabled:
                ledger_mod.add_audit_entry(
                    db,
                    actor_id="CONTROLLED_TEST",
                    action="INGEST_ECG",
                    details={"record_id": new_record.id},
                    auto_commit=False
                )
            
            db.commit()
            
        except Exception as e:
            print(f"Error: {e}")
            db.rollback()
        
        insert_time = (time.perf_counter() - start) * 1000
        insert_times.append(insert_time)
        
        if (i + 1) % 1000 == 0:
            print(f"  {mode}: {i+1}/{len(records)} - Avg: {np.mean(insert_times):.2f}ms")
    
    total_time = (time.perf_counter() - start_total) * 1000
    db.close()
    
    return {
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


def main():
    print("=" * 70)
    print("CONTROLLED LEDGER PERFORMANCE EXPERIMENT")
    print("=" * 70)
    
    # Load records
    with open(RESULTS_FILE, 'r') as f:
        records = [json.loads(line) for line in f if line.strip()]
    
    print(f"\nLoaded {len(records)} records")
    
    # Limit for faster testing (remove for full run)
    LIMIT = None  # Set to None for full 8528 records
    
    # === EXPERIMENT 1: LEDGER OFF ===
    print("\n" + "=" * 70)
    print("PHASE 1: LEDGER OFF")
    print("=" * 70)
    
    print("Clearing database...")
    clear_database()
    
    # Wait for system to stabilize
    stats_off = wait_for_stable_system()
    
    print("Running Ledger OFF experiment...")
    results_off = run_single_experiment(records, ledger_enabled=False, limit=LIMIT)
    results_off['system_stats_before'] = stats_off
    
    print(f"\nLEDGER OFF Complete:")
    print(f"  Total Time: {results_off['total_time_ms']/1000:.2f}s")
    print(f"  Avg Insert: {results_off['avg_insert_time_ms']:.3f}ms")
    print(f"  Median Insert: {results_off['median_insert_time_ms']:.3f}ms")
    
    # === EXPERIMENT 2: LEDGER ON ===
    print("\n" + "=" * 70)
    print("PHASE 2: LEDGER ON")
    print("=" * 70)
    
    print("Clearing database...")
    clear_database()
    
    # Force GC and wait for system to stabilize (match Phase 1 RAM)
    gc.collect()
    stats_on = wait_for_stable_system(prev_ram=stats_off['ram_free_mb'])
    
    print("Running Ledger ON experiment...")
    results_on = run_single_experiment(records, ledger_enabled=True, limit=LIMIT)
    results_on['system_stats_before'] = stats_on
    
    print(f"\nLEDGER ON Complete:")
    print(f"  Total Time: {results_on['total_time_ms']/1000:.2f}s")
    print(f"  Avg Insert: {results_on['avg_insert_time_ms']:.3f}ms")
    print(f"  Median Insert: {results_on['median_insert_time_ms']:.3f}ms")
    
    # === COMPARISON ===
    print("\n" + "=" * 70)
    print("COMPARISON RESULTS")
    print("=" * 70)
    
    overhead_avg = (results_on['avg_insert_time_ms'] - results_off['avg_insert_time_ms'])
    overhead_median = (results_on['median_insert_time_ms'] - results_off['median_insert_time_ms'])
    overhead_pct = (results_on['median_insert_time_ms'] / results_off['median_insert_time_ms'] - 1) * 100
    
    print(f"\n{'Metric':<25} {'Ledger OFF':>15} {'Ledger ON':>15} {'Overhead':>15}")
    print("-" * 70)
    print(f"{'Total Time (s)':<25} {results_off['total_time_ms']/1000:>15.2f} {results_on['total_time_ms']/1000:>15.2f}")
    print(f"{'Avg Insert (ms)':<25} {results_off['avg_insert_time_ms']:>15.3f} {results_on['avg_insert_time_ms']:>15.3f} {overhead_avg:>+15.3f}")
    print(f"{'Median Insert (ms)':<25} {results_off['median_insert_time_ms']:>15.3f} {results_on['median_insert_time_ms']:>15.3f} {overhead_median:>+15.3f}")
    print(f"{'Records/Second':<25} {results_off['records_per_second']:>15.1f} {results_on['records_per_second']:>15.1f}")
    print(f"\nLedger Overhead: {overhead_pct:+.2f}%")
    
    # Save results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    comparison = {
        "ledger_off": results_off,
        "ledger_on": results_on,
        "overhead_ms": overhead_median,
        "overhead_percent": overhead_pct,
        "timestamp": dt.datetime.now(dt.UTC).isoformat()
    }
    
    with open(OUTPUT_DIR / "controlled_comparison.json", 'w') as f:
        json.dump(comparison, f, indent=2)
    
    print(f"\nResults saved to: {OUTPUT_DIR / 'controlled_comparison.json'}")


if __name__ == '__main__':
    main()
