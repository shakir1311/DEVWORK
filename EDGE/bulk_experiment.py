"""
Bulk Experiment Module for EDGE
Runs inference on all PhysioNet CinC 2017 records and inserts results to Portal DB.

Optimizations:
- Parallel file preloading (ThreadPoolExecutor)
- Batch DB commits (every 20 records)
- Memory-efficient streaming (no in-memory result accumulation)
"""

import os
import sys
import csv
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, Future

import threading

import numpy as np
import scipy.io
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

# Configuration
BATCH_COMMIT_SIZE = 20  # Commit DB every N records
PRELOAD_BUFFER_SIZE = 5  # Number of files to preload ahead


@dataclass
class ExperimentResult:
    patient_id: str
    ground_truth: str
    predicted_class: str = "?"
    confidence: float = 0.0
    heart_rate: float = 0.0
    success: bool = False
    error_message: str = ""


class BulkExperimentWorker(QThread):
    """
    Background worker for bulk ECG experiment.
    Loads all ECG files, runs inference, inserts to Portal DB.
    """
    
    sig_progress = pyqtSignal(int, int, str)  # current, total, patient_id
    sig_status = pyqtSignal(str)  # status message
    sig_error = pyqtSignal(str)  # error message
    sig_finished = pyqtSignal(dict)  # summary
    
    # PhysioNet CinC 2017 rhythm classes
    RHYTHM_CLASSES = {
        'N': 'Normal sinus rhythm',
        'A': 'Atrial Fibrillation',
        'O': 'Other rhythm',
        '~': 'Noisy (too noisy to classify)'
    }
    
    def __init__(self, processor_pipeline, dataset_dir: str = None):
        super().__init__()
        self.processor_pipeline = processor_pipeline
        
        # Dataset location - try DataSimulator's cached dataset first
        if dataset_dir:
            self.dataset_dir = Path(dataset_dir)
        else:
            # Default: look for DataSimulator's dataset (in data/cinc2017/)
            self.dataset_dir = Path(__file__).parent.parent / "DataSimulator" / "data" / "cinc2017"
        
        self.reference_file = self.dataset_dir / "REFERENCE.csv"
        self.stop_requested = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # Start in un-paused (running) state
        self.results: List[ExperimentResult] = []
        self.inference_results: List[Dict] = []
        
    def configure(self, experiment_name: str = "bulk_experiment", ledger_enabled: bool = True, xai_enabled: bool = True):
        """Configure experiment parameters.
        
        Args:
            experiment_name: Name for the experiment
            ledger_enabled: Whether to log to audit ledger
            xai_enabled: Whether to generate XAI explanations (disable for speed)
        """
        self.experiment_name = experiment_name
        self.ledger_enabled = ledger_enabled
        self.xai_enabled = xai_enabled
        
    def stop(self):
        """Request stop. Also unblocks pause so the loop can exit."""
        self.stop_requested = True
        self._pause_event.set()
    
    def pause(self):
        """Pause the experiment loop after the current record finishes."""
        self._pause_event.clear()
        self.sig_status.emit("Experiment paused")
    
    def resume(self):
        """Resume a paused experiment."""
        self._pause_event.set()
        self.sig_status.emit("Experiment resumed")
        
    def run(self):
        """
        Main experiment loop with optimizations:
        - Parallel file preloading
        - Batch DB commits
        - Optional XAI generation
        """
        self.stop_requested = False
        self.results = []  # Not used in streaming mode
        self.inference_results = []
        start_time = time.time()
        correct_count = 0  # Track accuracy locally
        total_processed = 0
        
        # Check dataset exists
        if not self.reference_file.exists():
            self.sig_error.emit(f"Dataset not found: {self.reference_file}")
            return
        
        # Load patient list from REFERENCE.csv
        patients = self._load_patients()
        if not patients:
            self.sig_error.emit("No patients loaded from REFERENCE.csv")
            return
            
        total_count = len(patients)
        self.sig_status.emit(f"Running inference + DB insert on {total_count} records...")
        
        # Configure XAI based on settings
        if self.processor_pipeline and hasattr(self, 'xai_enabled'):
            # Find ML inference processor and set skip_xai flag
            for processor in self.processor_pipeline.processors:
                if hasattr(processor, 'skip_xai'):
                    processor.skip_xai = not self.xai_enabled
                    if not self.xai_enabled:
                        logger.info("[BulkExperiment] XAI generation disabled for speed")
        
        # Initialize DB session once (for efficiency)
        db, models_module, ledger_module = self._get_db_session()
        if db is None:
            self.sig_error.emit("Failed to connect to Portal database")
            return
        
        total_insert_time_ms = 0.0
        inserted_count = 0
        pending_commit = 0  # Track records since last commit
        
        # Prepare results directory and file
        results_dir = Path(__file__).parent / "experiment_results"
        results_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ledger_status = "ledger_on" if self.ledger_enabled else "ledger_off"
        xai_status = "xai_on" if getattr(self, 'xai_enabled', True) else "xai_off"
        experiment_dir = results_dir / f"{ledger_status}_{xai_status}_{timestamp}"
        experiment_dir.mkdir(exist_ok=True)
        results_file = experiment_dir / "results.jsonl"
        
        self.sig_status.emit(f"Results will be saved to: {results_file}")
        
        # Setup parallel file preloading
        preload_executor = ThreadPoolExecutor(max_workers=PRELOAD_BUFFER_SIZE)
        preload_futures: Dict[str, Future] = {}
        
        def preload_file(patient_id: str) -> Tuple[np.ndarray, Dict]:
            """Load ECG file in background thread."""
            return self._load_ecg(patient_id)
        
        # Start preloading first batch
        for i in range(min(PRELOAD_BUFFER_SIZE, len(patients))):
            patient_id = patients[i][0]
            preload_futures[patient_id] = preload_executor.submit(preload_file, patient_id)
        
        # Open results file in append mode
        with open(results_file, 'a') as f_results:
            
            # ========== PROCESS EACH RECORD ==========
            for idx, (patient_id, ground_truth) in enumerate(patients):
                # Block here while paused; unblocks instantly when not paused
                self._pause_event.wait()
                
                if self.stop_requested:
                    self.sig_status.emit("Experiment stopped by user")
                    break
                
                self.sig_progress.emit(idx + 1, total_count, patient_id)
                
                result = ExperimentResult(patient_id, ground_truth)
                
                try:
                    # Get ECG data from preload cache or load directly
                    if patient_id in preload_futures:
                        future = preload_futures.pop(patient_id)
                        ecg_values, metadata = future.result()
                    else:
                        ecg_values, metadata = self._load_ecg(patient_id)
                    
                    # Schedule next file to preload
                    next_preload_idx = idx + PRELOAD_BUFFER_SIZE
                    if next_preload_idx < len(patients):
                        next_patient_id = patients[next_preload_idx][0]
                        if next_patient_id not in preload_futures:
                            preload_futures[next_patient_id] = preload_executor.submit(preload_file, next_patient_id)
                    
                    # Run inference
                    if self.processor_pipeline:
                        processing_results = self.processor_pipeline.process(ecg_values, metadata)
                        
                        ml_results = processing_results.get('results', {}).get('ml_inference', {})
                        hr_results = processing_results.get('results', {}).get('heart_rate', {})
                        
                        result.predicted_class = ml_results.get('classification', '?')
                        result.confidence = ml_results.get('confidence', 0.0)
                        result.heart_rate = hr_results.get('heart_rate_bpm', 0.0)
                        result.success = True
                        
                        # Insert to DB (no auto commit - we batch it)
                        insert_time = self._insert_single_record(
                            db, models_module, ledger_module,
                            patient_id, ecg_values.tolist(), 
                            result.predicted_class, result.confidence, 
                            result.heart_rate, processing_results,
                            auto_commit=False  # Batch commits
                        )
                        total_insert_time_ms += insert_time
                        inserted_count += 1
                        pending_commit += 1
                        
                        # Batch commit every BATCH_COMMIT_SIZE records
                        if pending_commit >= BATCH_COMMIT_SIZE:
                            db.commit()
                            pending_commit = 0
                        
                except Exception as e:
                    result.error_message = str(e)
                    logger.error(f"Error processing {patient_id}: {e}")
                
                # Write to JSONL immediately
                json_line = json.dumps({
                    "patient_id": result.patient_id,
                    "ground_truth": result.ground_truth,
                    "predicted_class": result.predicted_class,
                    "confidence": result.confidence,
                    "success": result.success,
                    "error": result.error_message
                })
                f_results.write(json_line + '\n')
                f_results.flush()  # Ensure it's written to disk
                
                # Track counts for summary generation
                total_processed += 1
                if result.success:
                    if result.predicted_class == result.ground_truth:
                        correct_count += 1
                
                # Clear memory periodically (every 100 records)
                if idx % 100 == 0:
                    import gc
                    gc.collect()
                    # Also clear PyTorch cache if available
                    try:
                        import torch
                        if hasattr(torch, 'mps') and torch.backends.mps.is_available():
                            torch.mps.empty_cache()
                        elif torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except:
                        pass
        
        # Cleanup preload executor
        preload_executor.shutdown(wait=False)
        
        # Commit any pending records from batch and close
        try:
            if pending_commit > 0:
                db.commit()  # Final batch
            db.close()
        except Exception as e:
            logger.error(f"Error closing DB: {e}")
        
        # Generate summary
        elapsed = time.time() - start_time
        portal_stats = {
            "inserted_count": inserted_count,
            "total_time_ms": total_insert_time_ms,
            "avg_time_per_record_ms": total_insert_time_ms / inserted_count if inserted_count > 0 else 0,
            "ledger_enabled": self.ledger_enabled
        }
        summary = self._generate_summary(elapsed, portal_stats, correct_count, total_processed, inserted_count)
        
        # Save summary
        with open(experiment_dir / "summary.json", 'w') as f:
            json.dump(summary, f, indent=2)
        
        logger.info(f"Results saved to {experiment_dir}")
        self.sig_finished.emit(summary)
    
    def _load_patients(self) -> List[tuple]:
        """Load patient list from REFERENCE.csv."""
        patients = []
        try:
            with open(self.reference_file, 'r') as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) >= 2:
                        patient_id = row[0].strip()
                        rhythm = row[1].strip()
                        if rhythm in self.RHYTHM_CLASSES:
                            patients.append((patient_id, rhythm))
            logger.info(f"Loaded {len(patients)} patients from REFERENCE.csv")
        except Exception as e:
            logger.error(f"Error loading REFERENCE.csv: {e}")
        return patients
    
    def _load_ecg(self, patient_id: str) -> tuple:
        """Load ECG data from .mat file."""
        # Files are in training/ subfolder with structure: training/A00/A00001.mat
        mat_file = self.dataset_dir / "training" / f"{patient_id}.mat"
        if not mat_file.exists():
            raise FileNotFoundError(f"ECG file not found: {mat_file}")
        
        mat_data = scipy.io.loadmat(str(mat_file))
        ecg_raw = mat_data['val'][0].astype(np.float32)
        
        # Convert to mV (PhysioNet CinC 2017 is in microvolts)
        ecg_mv = ecg_raw / 1000.0
        
        sampling_rate = 300  # Fixed for CinC 2017
        duration = len(ecg_mv) / sampling_rate
        
        metadata = {
            'patient_info': {'patient_id': patient_id},
            'sampling_rate': sampling_rate,
            'total_samples': len(ecg_mv),
            'duration_seconds': duration,
        }
        
        return ecg_mv, metadata
    
    def _get_db_session(self):
        """Initialize DB session using Web's actual database file."""
        portal_dir = Path(__file__).parent.parent / "Web"
        db_path = portal_dir / "portal.db"
        
        try:
            from sqlalchemy import create_engine, event
            from sqlalchemy.orm import sessionmaker
            
            engine = create_engine(
                f"sqlite:///{db_path}",
                connect_args={"check_same_thread": False, "timeout": 30}
            )
            
            @event.listens_for(engine, "connect")
            def _set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()
            
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            
            # Import models and ledger from Web
            if str(portal_dir) not in sys.path:
                sys.path.insert(0, str(portal_dir))
            import models
            import ledger
            
            return SessionLocal(), models, ledger
        except Exception as e:
            logger.error(f"Failed to connect to DB: {e}")
            return None, None, None
    
    def _insert_single_record(self, db, models, ledger_mod, patient_id, ecg_values, 
                               classification, confidence, heart_rate, results,
                               auto_commit: bool = True):
        """Insert a single record to DB. Returns insert time in ms.
        
        Args:
            auto_commit: If True, commits immediately. If False, caller handles commits (batch mode).
        """
        import datetime as dt
        
        start = time.time()
        
        try:
            # Find or create patient
            patient = db.query(models.Patient).filter(
                models.Patient.patient_id_external == patient_id
            ).first()
            if not patient:
                patient = models.Patient(
                    patient_id_external=patient_id,
                    name=f"Patient {patient_id}",
                    dob="Unknown"
                )
                db.add(patient)
                db.flush()
            
            # Extract hashes for provenance
            ml_results = results.get('results', {}).get('ml_inference', {})
            model_version = ml_results.get('model_version')
            model_hash = ml_results.get('model_hash')
            data_hash = ml_results.get('data_hash')

            # Create ECG record
            new_record = models.ECGRecord(
                patient_id=patient.id,
                timestamp=dt.datetime.utcnow(),
                device_id="BULK_EXPERIMENT",
                heart_rate=heart_rate,
                classification=classification,
                confidence=confidence,
                model_version=model_version,
                model_hash=model_hash,
                data_hash=data_hash,
                ecg_data=ecg_values,
                processing_results=results
            )
            db.add(new_record)
            db.flush()
            
            # Add ledger entry if enabled
            if self.ledger_enabled:
                ledger_mod.add_audit_entry(
                    db,
                    actor_id="BULK_EXPERIMENT",
                    action="INGEST_ECG",
                    details={
                        "record_id": new_record.id, 
                        "patient": patient_id,
                        "class": classification,
                        "model_version": model_version,
                        "model_hash": model_hash,
                        "data_hash": data_hash
                    },
                    auto_commit=auto_commit  # Pass through for batch mode
                )
            
            # Commit only if auto_commit is True
            if auto_commit:
                db.commit()
            
        except Exception as e:
            logger.error(f"Error inserting {patient_id}: {e}")
            db.rollback()
        
        return (time.time() - start) * 1000
    
    def _bulk_insert_to_db(self) -> Dict:
        """Insert all results directly to Portal's SQLite database."""
        import datetime as dt
        
        # Add Portal to path
        portal_dir = Path(__file__).parent.parent / "Web"
        if str(portal_dir) not in sys.path:
            sys.path.insert(0, str(portal_dir))
        
        try:
            from database import SessionLocal
            import models
            import ledger
            
            db = SessionLocal()
            start_time = time.time()
            inserted_count = 0
            
            for rec in self.inference_results:
                try:
                    patient_id = rec['patient_id']
                    
                    # Find or create patient
                    patient = db.query(models.Patient).filter(
                        models.Patient.patient_id_external == patient_id
                    ).first()
                    if not patient:
                        patient = models.Patient(
                            patient_id_external=patient_id,
                            name=f"Patient {patient_id}",
                            dob="Unknown"
                        )
                        db.add(patient)
                        db.flush()
                    
                    # Extract hashes for provenance
                    ml_results = rec.get('results', {}).get('ml_inference', {})
                    model_version = ml_results.get('model_version')
                    model_hash = ml_results.get('model_hash')
                    data_hash = ml_results.get('data_hash')

                    # Create ECG record
                    new_record = models.ECGRecord(
                        patient_id=patient.id,
                        timestamp=dt.datetime.utcnow(),
                        device_id="BULK_EXPERIMENT",
                        heart_rate=rec.get('heart_rate', 0.0),
                        classification=rec.get('classification', '?'),
                        confidence=rec.get('confidence', 0.0),
                        model_version=model_version,
                        model_hash=model_hash,
                        data_hash=data_hash,
                        ecg_data=rec.get('ecg_values', []),
                        processing_results=rec.get('results', {})
                    )
                    db.add(new_record)
                    db.flush()
                    
                    # Add ledger entry if enabled
                    if self.ledger_enabled:
                        ledger.add_audit_entry(
                            db,
                            actor_id="BULK_EXPERIMENT",
                            action="INGEST_ECG",
                            details={
                                "record_id": new_record.id, 
                                "patient": patient_id,
                                "class": rec.get('classification'),
                                "model_version": model_version,
                                "model_hash": model_hash,
                                "data_hash": data_hash
                            }
                        )
                    
                    inserted_count += 1
                    
                except Exception as e:
                    logger.error(f"Error inserting {rec.get('patient_id')}: {e}")
            
            db.commit()
            db.close()
            
            total_time_ms = (time.time() - start_time) * 1000
            avg_time = total_time_ms / inserted_count if inserted_count > 0 else 0
            
            logger.info(f"DB insert: {inserted_count} records in {total_time_ms:.1f}ms")
            
            return {
                "inserted_count": inserted_count,
                "total_time_ms": total_time_ms,
                "avg_time_per_record_ms": avg_time,
                "ledger_enabled": self.ledger_enabled
            }
            
        except Exception as e:
            logger.error(f"DB insert failed: {e}")
            return {"error": str(e)}
    
    def _generate_summary(self, elapsed_seconds: float, portal_stats: Dict, correct_count: int = 0, total_processed: int = 0, inserted_count: int = 0) -> Dict:
        """Generate experiment summary using passed counters (not self.results)."""
        accuracy = correct_count / inserted_count if inserted_count > 0 else 0.0
        
        return {
            "experiment_name": self.experiment_name,
            "timestamp": datetime.now().isoformat(),
            "total_records": total_processed,
            "successful_inferences": inserted_count,
            "accuracy": accuracy,
            "correct_predictions": correct_count,
            "inference_time_seconds": elapsed_seconds - (portal_stats.get('total_time_ms', 0) / 1000),
            "portal_insert": portal_stats,
            "ledger_enabled": self.ledger_enabled
        }

