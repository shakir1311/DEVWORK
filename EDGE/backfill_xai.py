#!/usr/bin/env python3
"""
XAI Backfill Script
Runs ECG-DualNet inference + GradCAM XAI explanations on all existing ECG records
in the portal database and updates processing_results with full ML + XAI data.

Usage:
    cd EDGE
    source venv/bin/activate
    python backfill_xai.py [--batch-size 100] [--limit 0] [--dry-run]

This script:
1. Loads the ECG-DualNet model
2. Iterates through all ecg_records in portal.db
3. Runs inference + XAI (GradCAM) on each record's ecg_data
4. Updates processing_results with the full results structure expected by the web portal

The web portal expects: processing_results.results.ml_inference.explanation
"""

import sys
import os
import json
import time
import sqlite3
import argparse
import logging
import numpy as np
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Add EDGE directory to path for imports
EDGE_DIR = Path(__file__).parent
sys.path.insert(0, str(EDGE_DIR))
sys.path.insert(0, str(EDGE_DIR / 'ecg_dualnet'))

# Import ML components
import torch
from ecg_dualnet_wrapper import get_pretrained_ecg_dualnet
from processors.xai_explainer import ECGExplainer

# Constants
FIXED_LENGTH = 9000  # 30 seconds @ 300Hz
CLASS_NAMES = ['N', 'A', 'O', '~']
CLASS_DESCRIPTIONS = {
    'N': 'Normal sinus rhythm',
    'A': 'Atrial Fibrillation',
    'O': 'Other rhythm',
    '~': 'Noisy (too noisy to classify)'
}
# ECG-DualNet class order: N=0, O=1, A=2, ~=3
# Our class order:          N=0, A=1, O=2, ~=3
DUALNET_TO_OURS = {0: 0, 1: 2, 2: 1, 3: 3}

DB_PATH = EDGE_DIR.parent / 'Web' / 'portal.db'


def preprocess_signal(ecg_values: list) -> np.ndarray:
    """Preprocess raw ECG values for inference (z-score normalize + fix length)."""
    signal = np.array(ecg_values, dtype=np.float32).flatten()
    
    # Z-score normalize
    mean = np.mean(signal)
    std = np.std(signal)
    if std > 0:
        signal = (signal - mean) / std
    else:
        signal = signal - mean
    
    # Fix length
    if len(signal) >= FIXED_LENGTH:
        signal = signal[:FIXED_LENGTH]
    else:
        signal = np.pad(signal, (0, FIXED_LENGTH - len(signal)), 'constant')
    
    return signal


def run_inference_and_xai(model, explainer, ecg_signal: np.ndarray) -> dict:
    """
    Run ECG-DualNet inference + XAI on a preprocessed signal.
    Returns the full results structure expected by the web portal.
    """
    # Inference
    x = torch.from_numpy(ecg_signal).float().unsqueeze(0).unsqueeze(0)  # (1, 1, 9000)
    
    with torch.no_grad():
        preds, probs = model.predict(x)
    
    dualnet_idx = preds.item()
    predicted_idx = DUALNET_TO_OURS[dualnet_idx]
    
    # Remap probabilities: DualNet [N, O, A, ~] -> Ours [N, A, O, ~]
    dualnet_probs = probs.cpu().numpy()[0]
    our_probs = np.array([
        dualnet_probs[0],  # N -> N
        dualnet_probs[2],  # A -> A
        dualnet_probs[1],  # O -> O
        dualnet_probs[3],  # ~ -> ~
    ])
    
    predicted_class = CLASS_NAMES[predicted_idx]
    confidence = float(our_probs[predicted_idx])
    
    # Build class probabilities dict
    class_probabilities = {
        name: float(prob) for name, prob in zip(CLASS_NAMES, our_probs)
    }
    
    # XAI explanation
    explanation = None
    try:
        xai_result = explainer.explain(ecg_signal, predicted_idx)
        explanation = {
            'signal_importance': xai_result['signal_importance'],
            'peak_regions': xai_result['peak_regions'],
            'explanation_text': xai_result['explanation_text']
        }
    except Exception as e:
        logger.warning(f"XAI failed: {e}")
    
    # Build the full results structure that the web portal expects
    # Portal template reads: results.results.ml_inference.explanation
    results = {
        'results': {
            'ml_inference': {
                'model_type': 'ecg_dualnet',
                'model_path': 'ECG-DualNet-S (Pretrained)',
                'classification': predicted_class,
                'classification_description': CLASS_DESCRIPTIONS.get(predicted_class, 'Unknown'),
                'confidence': confidence,
                'probabilities': [float(p) for p in our_probs],
                'class_probabilities': class_probabilities,
                'explanation': explanation,
            }
        }
    }
    
    return results, predicted_class, confidence


def main():
    parser = argparse.ArgumentParser(description='Backfill XAI explanations for ECG records')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Number of records to process before committing (default: 100)')
    parser.add_argument('--limit', type=int, default=0,
                        help='Max records to process (0 = all)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Run without writing to database')
    parser.add_argument('--start-id', type=int, default=0,
                        help='Start from this record ID (for resuming)')
    parser.add_argument('--db', type=str, default=str(DB_PATH),
                        help=f'Database path (default: {DB_PATH})')
    args = parser.parse_args()
    
    db_path = Path(args.db)
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        sys.exit(1)
    
    logger.info("=" * 60)
    logger.info("XAI Backfill Script")
    logger.info("=" * 60)
    
    # --- Load Model ---
    logger.info("Loading ECG-DualNet-S model...")
    pretrained_path = EDGE_DIR / 'ecg_dualnet' / 'pretrained' / 'ECGCNN_S_best_model.pt'
    if not pretrained_path.exists():
        logger.error(f"Model weights not found: {pretrained_path}")
        sys.exit(1)
    
    model = get_pretrained_ecg_dualnet(model_size='S', device='cpu', model_path=str(pretrained_path))
    model.eval()
    logger.info("✓ Model loaded")
    
    # --- Create Explainer ---
    explainer = ECGExplainer(model, device='cpu')
    logger.info("✓ XAI Explainer ready (GradCAM)")
    
    # --- Connect to DB ---
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()
    
    # Count records
    where_clause = f"WHERE id > {args.start_id}" if args.start_id > 0 else ""
    cursor.execute(f"SELECT COUNT(*) FROM ecg_records {where_clause}")
    total = cursor.fetchone()[0]
    
    if args.limit > 0:
        total = min(total, args.limit)
    
    logger.info(f"Records to process: {total}")
    if args.dry_run:
        logger.info("*** DRY RUN - no database writes ***")
    
    # --- Process Records ---
    query = f"""
        SELECT id, ecg_data 
        FROM ecg_records 
        {where_clause}
        ORDER BY id ASC
    """
    if args.limit > 0:
        query += f" LIMIT {args.limit}"
    
    cursor.execute(query)
    
    processed = 0
    errors = 0
    start_time = time.time()
    batch_updates = []
    
    for row in cursor:
        record_id, ecg_data_raw = row
        
        try:
            # Parse ECG data
            ecg_values = json.loads(ecg_data_raw) if isinstance(ecg_data_raw, str) else ecg_data_raw
            
            if not ecg_values or len(ecg_values) < 100:
                logger.warning(f"Record {record_id}: insufficient ECG data ({len(ecg_values) if ecg_values else 0} samples)")
                errors += 1
                continue
            
            # Preprocess
            ecg_signal = preprocess_signal(ecg_values)
            
            # Run inference + XAI
            results, pred_class, conf = run_inference_and_xai(model, explainer, ecg_signal)
            
            # Queue update
            if not args.dry_run:
                batch_updates.append((
                    json.dumps(results),
                    pred_class,
                    conf,
                    record_id
                ))
            
            processed += 1
            
            # Commit batch
            if len(batch_updates) >= args.batch_size:
                conn2 = sqlite3.connect(str(db_path))
                conn2.execute("PRAGMA journal_mode=WAL")
                conn2.executemany(
                    "UPDATE ecg_records SET processing_results = ?, classification = ?, confidence = ? WHERE id = ?",
                    batch_updates
                )
                conn2.commit()
                conn2.close()
                batch_updates = []
            
            # Progress logging
            if processed % 50 == 0 or processed == total:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                eta = (total - processed) / rate if rate > 0 else 0
                logger.info(
                    f"Progress: {processed}/{total} ({processed/total*100:.1f}%) | "
                    f"Rate: {rate:.1f} rec/s | "
                    f"ETA: {eta/60:.1f} min | "
                    f"Errors: {errors}"
                )
                
        except Exception as e:
            logger.error(f"Record {record_id}: {e}")
            errors += 1
    
    # Commit remaining
    if batch_updates and not args.dry_run:
        conn2 = sqlite3.connect(str(db_path))
        conn2.execute("PRAGMA journal_mode=WAL")
        conn2.executemany(
            "UPDATE ecg_records SET processing_results = ?, classification = ?, confidence = ? WHERE id = ?",
            batch_updates
        )
        conn2.commit()
        conn2.close()
    
    conn.close()
    
    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info(f"COMPLETE: {processed}/{total} records processed in {elapsed/60:.1f} min")
    logger.info(f"  Rate: {processed/elapsed:.1f} records/sec" if elapsed > 0 else "")
    logger.info(f"  Errors: {errors}")
    if args.dry_run:
        logger.info("  (DRY RUN - nothing written)")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
