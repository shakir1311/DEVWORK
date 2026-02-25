"""
Batch Simulator for Automation Experiments
Processes all ECG records sequentially through the BIEIF-RPM pipeline.
"""

import time
import json
import struct
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
import logging
from PyQt6.QtCore import QThread, pyqtSignal
import numpy as np

from ecg_simulator import ECGSimulator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ExperimentResult:
    """Container for a single experiment record result."""
    def __init__(self, patient_id: str, ground_truth: str):
        self.patient_id = patient_id
        self.ground_truth = ground_truth
        self.predicted_class: Optional[str] = None
        self.confidence: float = 0.0
        self.t_publish: int = 0  # Unix ms
        self.t_portal_insert: int = 0  # Unix ms
        self.latency_ms: float = 0.0
        self.portal_record_id: int = 0
        self.success: bool = False
        self.error_message: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "patient_id": self.patient_id,
            "ground_truth": self.ground_truth,
            "predicted_class": self.predicted_class,
            "confidence": self.confidence,
            "t_publish": self.t_publish,
            "t_portal_insert": self.t_portal_insert,
            "latency_ms": self.latency_ms,
            "portal_record_id": self.portal_record_id,
            "success": self.success,
            "error_message": self.error_message
        }


class BatchSimulatorWorker(QThread):
    """
    Background worker for batch processing all ECG records.
    
    Signals:
        sig_progress: (current_index, total_count, patient_id) - Progress update
        sig_record_complete: (result_dict) - Single record completed
        sig_status: (message) - Status message
        sig_error: (message) - Error occurred
        sig_finished: (summary_dict) - Batch complete
    """
    
    sig_progress = pyqtSignal(int, int, str)  # current, total, patient_id
    sig_record_complete = pyqtSignal(dict)    # result dict
    sig_status = pyqtSignal(str)              # status message
    sig_error = pyqtSignal(str)               # error message
    sig_finished = pyqtSignal(dict)           # summary
    
    # Payload format
    PAYLOAD_FORMAT_VERSION = 3
    CHUNK_HEADER_FORMAT = "<HHHI"  # version, rate, chunk_num, total_chunks, sample_count
    CHUNK_SIZE_SAMPLES = 600
    
    def __init__(self, simulator: ECGSimulator, portal_url: str = "http://localhost:8000",
                 edge_url: str = "http://localhost:5001"):
        super().__init__()
        self.simulator = simulator
        self.portal_url = portal_url
        self.edge_url = edge_url
        
        # Configuration
        self.min_delay_seconds: float = 0.0
        self.experiment_name: str = "ledger_on"
        self.results_dir: Path = Path("experiment_results")
        
        # State
        self.stop_requested = False
        self.results: List[ExperimentResult] = []
        self.start_time: float = 0
        
        # ACK handling (same as simulator_worker.py)
        import threading
        self.ack_received = threading.Event()
        self.last_ack_chunk = -1
        self.ack_lock = threading.Lock()
        self.ACK_TOPIC = "ecg/ack"
        
    def configure(self, 
                  min_delay_seconds: float = 0.0,
                  experiment_name: str = "ledger_on",
                  results_dir: str = "experiment_results",
                  portal_url: str = "http://localhost:8000",
                  edge_url: str = "http://localhost:5001"):
        """Configure batch parameters before running."""
        self.min_delay_seconds = min_delay_seconds
        self.experiment_name = experiment_name
        self.results_dir = Path(results_dir)
        self.portal_url = portal_url
        self.edge_url = edge_url
        
    def stop(self):
        """Request stop of batch processing."""
        self.stop_requested = True
        self.sig_status.emit("Stop requested, finishing current record...")
        
    def run(self):
        """
        Main batch processing loop - Two-Phase approach:
        Phase 1: Run inference on all records via EDGE (collect results locally)
        Phase 2: Bulk upload all records to Portal (measure ledger performance)
        """
        self.stop_requested = False
        self.results = []
        self.inference_results = []  # Store for bulk upload
        self.start_time = time.time()
        
        # Auto-append timestamp to experiment name for unique folder per run
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        timestamped_name = f"{self.experiment_name}_{timestamp}"
        
        # Ensure results directory exists
        experiment_dir = self.results_dir / timestamped_name
        experiment_dir.mkdir(parents=True, exist_ok=True)
        self.actual_experiment_folder = timestamped_name
        
        # Get patient list
        patients = self.simulator.list_patients()
        total_count = len(patients)
        
        self.sig_status.emit(f"Phase 1: Running inference on {total_count} records...")
        
        # ========== PHASE 1: INFERENCE ==========
        results_file = experiment_dir / "results.jsonl"
        
        for idx, patient_id in enumerate(patients):
            if self.stop_requested:
                self.sig_status.emit("Batch stopped by user")
                break
                
            self.sig_progress.emit(idx + 1, total_count, patient_id)
            
            # Get ground truth
            patient_info = self.simulator.get_patient_info(patient_id)
            ground_truth = patient_info.get('rhythm', '?') if patient_info else '?'
            
            result = ExperimentResult(patient_id, ground_truth)
            
            try:
                # Load ECG data
                ecg_values, metadata = self.simulator.load_ecg(patient_id)
                
                # Send to EDGE for inference only (no Portal upload yet)
                edge_response = requests.post(
                    f"{self.edge_url}/api/inference-only",
                    json={
                        "patient_id": patient_id,
                        "ecg_values": ecg_values.tolist(),
                        "sampling_rate": metadata.get('sampling_rate', 300),
                        "duration_seconds": metadata.get('duration_seconds', 0),
                    },
                    timeout=30
                )
                
                if edge_response.status_code == 200:
                    edge_data = edge_response.json()
                    result.predicted_class = edge_data.get('classification', '?')
                    result.confidence = edge_data.get('confidence', 0.0)
                    result.success = True
                    
                    # Store for bulk upload
                    self.inference_results.append({
                        'patient_id': patient_id,
                        'ecg_values': ecg_values.tolist(),
                        'classification': result.predicted_class,
                        'confidence': result.confidence,
                        'heart_rate': edge_data.get('heart_rate', 0.0),
                        'metadata': metadata,
                        'results': edge_data.get('full_results', {})
                    })
                else:
                    result.error_message = f"EDGE error: {edge_response.status_code}"
                    
            except Exception as e:
                result.error_message = str(e)
                logger.error(f"Error processing {patient_id}: {e}")
            
            self.results.append(result)
            self.sig_record_complete.emit(result.to_dict())
            
            # Save result immediately
            with open(results_file, 'a') as f:
                f.write(json.dumps(result.to_dict()) + '\n')
        
        if self.stop_requested:
            return
            
        # ========== PHASE 2: BULK PORTAL UPLOAD ==========
        self.sig_status.emit(f"Phase 2: Bulk uploading {len(self.inference_results)} records to Portal...")
        
        portal_stats = self._bulk_upload_to_portal()
        
        # Generate summary
        elapsed = time.time() - self.start_time
        summary = self._generate_summary(elapsed, portal_stats)
        
        # Save summary
        with open(experiment_dir / "summary.json", 'w') as f:
            json.dump(summary, f, indent=2)
            
        self.sig_finished.emit(summary)
    
    def _bulk_upload_to_portal(self) -> Dict:
        """
        Insert all inference results directly to Portal's SQLite database.
        Uses direct SQLAlchemy for speed, measures ledger on/off performance.
        """
        import sys
        import os
        import datetime
        
        # Add Portal directory to path so we can import its models
        portal_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'Web'))
        if portal_dir not in sys.path:
            sys.path.insert(0, portal_dir)
        
        try:
            # Import Portal's database and models
            from database import SessionLocal, engine
            import models
            import ledger
            
            # Check if ledger is enabled (read from env like Portal does)
            ledger_enabled = os.getenv("LEDGER_ENABLED", "true").lower() == "true"
            
            db = SessionLocal()
            
            start_time = time.time()
            inserted_count = 0
            record_ids = []
            
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
                    
                    # Extract Provenance Hashes
                    ml_results = rec.get('results', {}).get('ml_inference', {})
                    model_version = ml_results.get('model_version')
                    model_hash = ml_results.get('model_hash')
                    data_hash = ml_results.get('data_hash')

                    # Create ECG record with raw waveform
                    new_record = models.ECGRecord(
                        patient_id=patient.id,
                        timestamp=datetime.datetime.utcnow(),
                        device_id="BATCH_EXPERIMENT",
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
                    record_ids.append(new_record.id)
                    
                    # Add ledger entry if enabled (this is what we're timing)
                    if ledger_enabled:
                        ledger.add_audit_entry(
                            db,
                            actor_id="BATCH_EXPERIMENT",
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
                    continue
            
            # Commit all at once
            db.commit()
            db.close()
            
            total_time_ms = (time.time() - start_time) * 1000
            avg_time_per_record = total_time_ms / inserted_count if inserted_count > 0 else 0
            
            logger.info(f"Direct DB insert: {inserted_count} records in {total_time_ms:.1f}ms (avg: {avg_time_per_record:.2f}ms/record)")
            
            return {
                "inserted_count": inserted_count,
                "total_time_ms": total_time_ms,
                "avg_time_per_record_ms": avg_time_per_record,
                "ledger_enabled": ledger_enabled,
                "record_ids": record_ids
            }
            
        except Exception as e:
            logger.error(f"Direct DB insert failed: {e}")
            return {"error": str(e)}
        
    def _process_patient(self, patient_id: str, expected_prev_record_id: int) -> ExperimentResult:
        """Process a single patient via direct HTTP to EDGE (bypasses IOT)."""
        # Get ground truth
        patient_info = self.simulator.get_patient_info(patient_id)
        ground_truth = patient_info.get('rhythm', '?') if patient_info else '?'
        
        result = ExperimentResult(patient_id, ground_truth)
        
        try:
            # Load ECG data
            ecg_values, metadata = self.simulator.load_ecg(patient_id)
            
            # Send directly to EDGE via HTTP (bypasses IOT layer)
            edge_response = requests.post(
                f"{self.edge_url}/api/batch-ingest",
                json={
                    "patient_id": patient_id,
                    "ecg_values": ecg_values.tolist(),
                    "sampling_rate": metadata.get('sampling_rate', 300),
                    "duration_seconds": metadata.get('duration_seconds', 0),
                    "record_date": metadata.get('record_date', ''),
                    "record_time": metadata.get('record_time', ''),
                },
                timeout=30
            )
            
            if edge_response.status_code == 200:
                edge_data = edge_response.json()
                # Get classification from EDGE response
                result.predicted_class = edge_data.get('classification', '?')
                result.confidence = edge_data.get('confidence', 0.0)
                
                # Wait for Portal to receive the record
                new_record_id, portal_timestamp = self._wait_for_portal_record(expected_prev_record_id)
                
                if new_record_id > 0:
                    result.portal_record_id = new_record_id
                    result.success = True
                else:
                    result.error_message = "Timeout waiting for portal record"
            else:
                result.error_message = f"EDGE error: {edge_response.status_code}"
                
        except Exception as e:
            result.error_message = str(e)
            logger.error(f"Error processing {patient_id}: {e}")
            
        return result
    
    def _on_ack_message(self, client, userdata, msg):
        """Handle acknowledgment messages from ESP32 (same as simulator_worker.py)."""
        try:
            if msg.payload is None or len(msg.payload) == 0:
                return
            payload_str = msg.payload.decode('utf-8').strip()
            if not payload_str:
                return
            chunk_num = int(payload_str)
            with self.ack_lock:
                self.last_ack_chunk = chunk_num
                self.ack_received.set()
            logger.debug(f"Received ACK for chunk {chunk_num}")
        except Exception as e:
            logger.warning(f"Error parsing ACK: {e}")
    
    def _publish_ecg_chunked(self, patient_id: str, ecg_values: np.ndarray, 
                              sampling_rate: int, metadata: dict):
        """Publish ECG data in chunks via MQTT, waiting for ACK after each."""
        import threading
        
        # Subscribe to ACK topic
        try:
            self.simulator.mqtt_client.subscribe(self.ACK_TOPIC, qos=0)
            self.simulator.mqtt_client.message_callback_add(self.ACK_TOPIC, self._on_ack_message)
        except Exception as e:
            logger.warning(f"Error subscribing to ACK topic: {e}")
        
        # Calculate chunks
        total_samples = len(ecg_values)
        total_chunks = (total_samples + self.CHUNK_SIZE_SAMPLES - 1) // self.CHUNK_SIZE_SAMPLES
        
        for chunk_num in range(total_chunks):
            if self.stop_requested:
                break
                
            start_idx = chunk_num * self.CHUNK_SIZE_SAMPLES
            end_idx = min(start_idx + self.CHUNK_SIZE_SAMPLES, total_samples)
            chunk_data = ecg_values[start_idx:end_idx]
            
            # Build payload (chunk_num is 0-based in payload)
            payload = self._build_chunk_payload(
                chunk_num, total_chunks, 
                chunk_data, sampling_rate, patient_id, metadata
            )
            
            # Reset ACK event
            self.ack_received.clear()
            
            # Publish to MQTT with QoS 1
            if self.simulator.mqtt_client and self.simulator.mqtt_client.is_connected():
                result = self.simulator.mqtt_client.publish("ecg/chunk", payload, qos=1)
                result.wait_for_publish(timeout=5)
            
            # Wait for ACK (3 second timeout like original)
            ack_timeout = 3.0
            ack_start = time.time()
            ack_received = False
            
            while (time.time() - ack_start) < ack_timeout and not self.stop_requested:
                try:
                    self.simulator.mqtt_client.loop_misc()
                except:
                    pass
                
                if self.ack_received.is_set():
                    with self.ack_lock:
                        if self.last_ack_chunk == chunk_num:
                            ack_received = True
                            break
                time.sleep(0.05)
            
            if not ack_received and not self.stop_requested:
                raise Exception(f"Timeout waiting for ACK for chunk {chunk_num + 1}/{total_chunks}")
            
    def _build_chunk_payload(self, chunk_num: int, total_chunks: int,
                              chunk_data: np.ndarray, sampling_rate: int,
                              patient_id: str, metadata: dict) -> bytes:
        """Build binary chunk payload matching simulator_worker.py format."""
        # Header: version(2), rate(2), chunk_num(2), total_chunks(2), sample_count(4)
        header = struct.pack(
            "<HHHHI",
            self.PAYLOAD_FORMAT_VERSION,
            sampling_rate,
            chunk_num,
            total_chunks,
            len(chunk_data)
        )
        
        # Body: CSV format matching simulator_worker.py
        csv_values = ','.join(f"{v:.6f}" for v in chunk_data)
        body_str = csv_values
        
        # Add patient info to chunk 0 (first chunk) - before ECG values
        if chunk_num == 0:
            patient_info = (
                f"PATIENT_INFO:{patient_id}|"
                f"DURATION:{metadata.get('duration_seconds', 0.0):.2f}|"
                f"SAMPLES:{metadata.get('samples', len(chunk_data))}|"
                f"DATE:{metadata.get('record_date', '')}|"
                f"TIME:{metadata.get('record_time', '')}\n"
            )
            body_str = patient_info + csv_values
        
        return header + body_str.encode('utf-8')
    
    def _get_latest_portal_record_id(self) -> int:
        """Get the latest record ID from portal."""
        response = requests.get(f"{self.portal_url}/api/system/latest-record-id", timeout=5)
        response.raise_for_status()
        return response.json().get('latest_id', 0)
    
    def _wait_for_portal_record(self, prev_record_id: int, timeout: float = 30.0) -> tuple:
        """
        Poll portal until a new record appears.
        Returns (new_record_id, timestamp_ms) or (0, 0) on timeout.
        """
        start_wait = time.time()
        
        while time.time() - start_wait < timeout:
            try:
                current_id = self._get_latest_portal_record_id()
                if current_id > prev_record_id:
                    # New record appeared
                    return current_id, int(time.time() * 1000)
            except Exception:
                pass
            time.sleep(0.3)  # Poll every 300ms
            
        return 0, 0
    
    def _get_portal_record(self, record_id: int) -> Optional[Dict]:
        """Fetch record details from portal."""
        try:
            response = requests.get(f"{self.portal_url}/api/ecg/{record_id}", timeout=5)
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None
    
    def _generate_summary(self, elapsed_seconds: float) -> Dict:
        """Generate batch summary statistics."""
        successful = [r for r in self.results if r.success]
        failed = [r for r in self.results if not r.success]
        
        latencies = [r.latency_ms for r in successful if r.latency_ms > 0]
        
        # Accuracy calculation
        correct = sum(1 for r in successful if r.predicted_class == r.ground_truth)
        accuracy = correct / len(successful) if successful else 0.0
        
        return {
            "experiment_name": self.experiment_name,
            "experiment_folder": getattr(self, 'actual_experiment_folder', self.experiment_name),
            "timestamp": datetime.now().isoformat(),
            "total_records": len(self.results),
            "successful": len(successful),
            "failed": len(failed),
            "elapsed_seconds": elapsed_seconds,
            "records_per_minute": len(self.results) / (elapsed_seconds / 60) if elapsed_seconds > 0 else 0,
            "accuracy": accuracy,
            "correct_predictions": correct,
            "latency_stats": {
                "mean_ms": np.mean(latencies) if latencies else 0,
                "median_ms": np.median(latencies) if latencies else 0,
                "std_ms": np.std(latencies) if latencies else 0,
                "min_ms": min(latencies) if latencies else 0,
                "max_ms": max(latencies) if latencies else 0,
                "p95_ms": np.percentile(latencies, 95) if latencies else 0,
                "p99_ms": np.percentile(latencies, 99) if latencies else 0,
            }
        }
