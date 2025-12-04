"""
ECG Simulator Model Layer
Handles PhysioNet CinC 2017 dataset management, patient records, and ECG data loading.
Note: Dataset download is handled by dataset_downloader.py
"""

import os
import csv
from typing import List, Optional, Dict, Tuple
import numpy as np
import scipy.io
import paho.mqtt.client as mqtt
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ECGSimulator:
    """Core data model for ECG dataset management and MQTT communication."""
    
    # Local paths
    DATASET_DIR = "./data/cinc2017"
    TRAINING_DIR = os.path.join(DATASET_DIR, "training")
    REFERENCE_FILE = os.path.join(DATASET_DIR, "REFERENCE.csv")
    
    # Sampling parameters
    ORIGINAL_FS = 300  # Original sampling rate from CinC 2017
    TARGET_FS = 100    # Default wearable sampling rate (will be variable)
    WINDOW_SIZE = 100  # 1 second @ 100 Hz (will be variable)
    
    # Rhythm classifications
    RHYTHM_CLASSES = {
        'N': 'Normal',
        'A': 'Atrial Fibrillation',
        'O': 'Other Rhythm',
        '~': 'Noisy'
    }
    
    def __init__(self, mqtt_broker: str = "localhost", mqtt_port: int = 1883):
        """
        Initialize ECG simulator.
        
        Args:
            mqtt_broker: MQTT broker address
            mqtt_port: MQTT broker port
        """
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.mqtt_client: Optional[mqtt.Client] = None
        self.patient_records: Dict[str, Dict] = {}
        
        # Check if dataset exists, but don't download automatically
        # Download will be handled by GUI with user confirmation
        if os.path.exists(self.REFERENCE_FILE):
            self._load_references()
        else:
            logger.info("Dataset not found. User will be prompted to download.")
            self.patient_records = {}
        
    def check_dataset_available(self) -> bool:
        """
        Check if REFERENCE.csv is available (individual files downloaded on demand).
        
        Returns:
            True if REFERENCE.csv exists, False otherwise
        """
        return os.path.exists(self.REFERENCE_FILE)
    
    def reload_dataset(self) -> None:
        """Reload patient records after dataset download."""
        if self.check_dataset_available():
            self._load_references()
    
    def _load_references(self) -> None:
        """Load patient records from REFERENCE CSV file."""
        try:
            with open(self.REFERENCE_FILE, 'r') as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) >= 2:
                        patient_id = row[0].strip()
                        rhythm = row[1].strip()
                        
                        if rhythm in self.RHYTHM_CLASSES:
                            self.patient_records[patient_id] = {
                                'rhythm': rhythm,
                                'rhythm_name': self.RHYTHM_CLASSES[rhythm]
                            }
                        else:
                            logger.warning(f"Unknown rhythm class '{rhythm}' for patient {patient_id}")
            
            logger.info(f"Loaded {len(self.patient_records)} patient records")
            
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Reference file not found: {self.REFERENCE_FILE}\n"
                "Please ensure dataset is downloaded correctly."
            )
        except Exception as e:
            logger.error(f"Error loading references: {str(e)}")
            raise
    
    def list_patients(self) -> List[str]:
        """
        Return sorted list of all patient IDs.
        
        Returns:
            List of patient IDs (e.g., ['A00001', 'A00002', ...])
        """
        return sorted(self.patient_records.keys())
    
    def get_patient_info(self, patient_id: str) -> Optional[Dict]:
        """
        Get detailed information about a specific patient.
        Downloads .mat file on demand if not already cached.
        
        Args:
            patient_id: Patient identifier (e.g., 'A00001')
            
        Returns:
            Dictionary with patient info or None if not found
        """
        try:
            if patient_id not in self.patient_records:
                return None
            
            # Ensure training directory exists
            os.makedirs(self.TRAINING_DIR, exist_ok=True)
            
            # Check if .mat file exists, download if needed
            mat_file = os.path.join(self.TRAINING_DIR, f"{patient_id}.mat")
            if not os.path.exists(mat_file):
                logger.info(f"Patient file {patient_id}.mat not found, downloading...")
                from dataset_downloader import DatasetDownloadWorker
                success = DatasetDownloadWorker.download_patient_file(patient_id)
                if not success:
                    logger.error(f"Failed to download {patient_id}.mat")
                    return None
            
            # Load .mat file to get signal info
            mat_data = scipy.io.loadmat(mat_file)
            ecg_raw = mat_data['val'][0]
            
            duration_seconds = len(ecg_raw) / self.ORIGINAL_FS
            
            return {
                'patient_id': patient_id,
                'rhythm': self.patient_records[patient_id]['rhythm'],
                'rhythm_name': self.patient_records[patient_id]['rhythm_name'],
                'duration_seconds': duration_seconds,
                'sampling_rate': self.ORIGINAL_FS,
                'samples': len(ecg_raw)
            }
            
        except Exception as e:
            logger.error(f"Error getting patient info for {patient_id}: {str(e)}")
            return None
    
    def load_ecg(self, patient_id: str) -> Tuple[np.ndarray, Dict]:
        """
        Load and preprocess ECG data for a patient.
        Downloads .mat and .hea files on demand if not already cached.
        Always uses 300 Hz sampling rate (no downsampling).
        Converts ADC values to actual voltage in mV using .hea file metadata.
        
        Args:
            patient_id: Patient identifier
            
        Returns:
            Tuple of (ecg_array_in_mV, metadata_dict)
            
        Raises:
            FileNotFoundError: If patient data not found
            Exception: If data loading fails
        """
        try:
            if patient_id not in self.patient_records:
                raise FileNotFoundError(f"Patient {patient_id} not found in dataset")
            
            # Ensure training directory exists
            os.makedirs(self.TRAINING_DIR, exist_ok=True)
            
            # Check if .mat and .hea files exist, download if needed
            mat_file = os.path.join(self.TRAINING_DIR, f"{patient_id}.mat")
            hea_file = os.path.join(self.TRAINING_DIR, f"{patient_id}.hea")
            
            if not os.path.exists(mat_file) or not os.path.exists(hea_file):
                logger.info(f"Patient files {patient_id}.mat/.hea not found, downloading...")
                from dataset_downloader import DatasetDownloadWorker
                success = DatasetDownloadWorker.download_patient_file(patient_id)
                if not success:
                    raise FileNotFoundError(f"Failed to download {patient_id} files")
            
            # Load .hea file metadata (required for ADC conversion)
            from hea_parser import HEAParser
            hea_metadata = HEAParser.load_patient_metadata(patient_id, self.TRAINING_DIR)
            
            if not hea_metadata:
                raise Exception(f"Failed to load .hea file metadata for {patient_id}")
            
            # Use sampling rate from .hea file (should be 300 Hz)
            sampling_rate = hea_metadata.sampling_frequency
            
            # Load ECG data (raw ADC values)
            mat_data = scipy.io.loadmat(mat_file)
            ecg_raw_adc = mat_data['val'][0]
            
            # Verify sample count matches .hea file
            if len(ecg_raw_adc) != hea_metadata.num_samples:
                logger.warning(f"Sample count mismatch: .mat has {len(ecg_raw_adc)}, .hea says {hea_metadata.num_samples}")
            
            # Convert ADC values to actual voltage in mV using .hea file conversion factor
            # ADC units format: "1000/mV" means 1000 ADC units = 1 mV
            adc_units_str = hea_metadata.adc_units
            if adc_units_str and '/' in adc_units_str:
                parts = adc_units_str.split('/')
                adc_per_mv = float(parts[0])  # e.g., 1000
                unit = parts[1]  # e.g., "mV"
                
                if unit != "mV":
                    logger.warning(f"Unexpected unit in ADC conversion: {unit}, expected mV")
                
                # Convert: voltage_mV = (adc_value - adc_zero) / adc_per_mv
                adc_zero = hea_metadata.adc_zero if hea_metadata.adc_zero is not None else 0
                ecg_mv = (ecg_raw_adc.astype(np.float32) - adc_zero) / adc_per_mv
                
                logger.debug(f"Converted ADC to mV: {adc_per_mv} ADC units = 1 mV, zero={adc_zero}")
            else:
                # Fallback: assume 1000 ADC units = 1 mV (standard for this dataset)
                logger.warning(f"Could not parse ADC units '{adc_units_str}', using default 1000/mV")
                ecg_mv = ecg_raw_adc.astype(np.float32) / 1000.0
            
            # No normalization - keep actual voltage values in mV
            # No noise added - send exact converted values for precise comparison
            ecg_final = ecg_mv.astype(np.float32)
            
            duration_seconds = len(ecg_final) / sampling_rate if sampling_rate > 0 else 0.0
            
            # Derive recording start/end timestamps from .hea metadata (if available)
            start_timestamp_ms = None
            if hea_metadata.time and hea_metadata.date:
                try:
                    # Example formats observed: "05:05:15" and "1/05/2000"
                    record_dt = datetime.strptime(
                        f"{hea_metadata.date} {hea_metadata.time}",
                        "%d/%m/%Y %H:%M:%S"
                    )
                    start_timestamp_ms = int(record_dt.timestamp() * 1000)
                except ValueError:
                    logger.warning(
                        "Unable to parse recording date/time from .hea file "
                        f"({hea_metadata.date} {hea_metadata.time})"
                    )
            
            duration_ms = int(round(duration_seconds * 1000))
            if start_timestamp_ms is None:
                start_timestamp_ms = 0  # Fallback if .hea missing/invalid
            end_timestamp_ms = start_timestamp_ms + duration_ms
            
            # Prepare metadata
            metadata = {
                'patient_id': patient_id,
                'rhythm': self.patient_records[patient_id]['rhythm'],
                'rhythm_name': self.patient_records[patient_id]['rhythm_name'],
                'samples': len(ecg_final),
                'sampling_rate': sampling_rate,  # Always 300 Hz from .hea file
                'duration_seconds': duration_seconds,
                'units': 'mV',  # Values are in millivolts
                'adc_units': hea_metadata.adc_units,
                'adc_zero': hea_metadata.adc_zero,
                'record_time': hea_metadata.time,
                'record_date': hea_metadata.date,
                'start_timestamp_ms': start_timestamp_ms,
                'end_timestamp_ms': end_timestamp_ms
            }
            
            logger.info(f"Loaded ECG for {patient_id}: {metadata['duration_seconds']:.1f}s @ {sampling_rate}Hz, "
                       f"range=[{np.min(ecg_final):.3f}, {np.max(ecg_final):.3f}] mV")
            
            return ecg_final, metadata
            
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error loading ECG for {patient_id}: {str(e)}")
            raise Exception(f"Failed to load ECG data: {str(e)}")
    
    def connect_mqtt(self) -> None:
        """
        Connect to MQTT broker.
        Reuses existing connection if already connected.
        
        Raises:
            Exception: If connection fails
        """
        # Check if already connected
        if self.mqtt_client and self.mqtt_client.is_connected():
            logger.info("MQTT client already connected, reusing existing connection")
            return
        
        # Disconnect old client if it exists but is not connected
        if self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except:
                pass  # Ignore errors when cleaning up old client
        
        try:
            # Create new MQTT client
            self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
            
            # Define callbacks
            def on_connect(client, userdata, flags, rc):
                if rc == 0:
                    logger.info(f"MQTT connected successfully to {self.mqtt_broker}:{self.mqtt_port}")
                else:
                    logger.error(f"MQTT connection failed with code {rc}")
            
            def on_disconnect(client, userdata, rc):
                if rc == 0:
                    logger.info("MQTT disconnected normally")
                else:
                    logger.warning(f"MQTT disconnected unexpectedly (code {rc}) - broker may be offline")
            
            def on_connect_fail(client, userdata):
                logger.error("MQTT connection failed - broker unreachable")
            
            def on_message(client, userdata, msg):
                """General message handler to catch any parsing errors."""
                try:
                    # This will be overridden by specific callbacks, but acts as a safety net
                    pass
                except Exception as e:
                    logger.error(f"Error in general message handler: {e}")
            
            self.mqtt_client.on_connect = on_connect
            self.mqtt_client.on_disconnect = on_disconnect
            self.mqtt_client.on_connect_fail = on_connect_fail
            self.mqtt_client.on_message = on_message
            
            # Connect
            logger.info(f"Attempting to connect to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
            
            # Wait for connection to establish and verify
            import time
            max_wait = 5  # Wait up to 5 seconds
            waited = 0
            while not self.mqtt_client.is_connected() and waited < max_wait:
                time.sleep(0.5)
                waited += 0.5
            
            if not self.mqtt_client.is_connected():
                raise Exception(f"Failed to establish MQTT connection within {max_wait} seconds")
            
            logger.info("MQTT connection established and verified")
            
        except Exception as e:
            logger.error(f"MQTT connection error: {str(e)}")
            raise Exception(
                f"Failed to connect to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}\n"
                f"Error: {str(e)}\n"
                "Please check that the broker is running and the address is correct."
            )
    
    def disconnect_mqtt(self) -> None:
        """Disconnect from MQTT broker."""
        if self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
                logger.info("MQTT disconnected")
            except Exception as e:
                logger.error(f"Error disconnecting MQTT: {str(e)}")

