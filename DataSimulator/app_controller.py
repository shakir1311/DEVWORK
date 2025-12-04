"""
Application Controller
Orchestrates Model + Worker Thread, validates inputs, manages state transitions.
"""

from typing import List, Optional, Dict, Tuple
import logging
from PyQt6.QtCore import QObject, pyqtSignal

from ecg_simulator import ECGSimulator
from simulator_worker import SimulatorWorker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SimulationController(QObject):
    """Controller for orchestrating ECG simulation components."""
    
    # Signals for GUI to listen to controller state changes
    sig_status_changed = pyqtSignal(str)  # State: "idle", "running", "paused", etc.
    sig_patient_list_updated = pyqtSignal(list)  # List of patient IDs
    sig_mqtt_connected = pyqtSignal(bool)  # True/False
    
    def __init__(self, mqtt_broker: str = "localhost", mqtt_port: int = 1883):
        """
        Initialize controller.
        
        Args:
            mqtt_broker: MQTT broker address
            mqtt_port: MQTT broker port
        """
        super().__init__()
        
        # Create simulator (will not auto-download dataset)
        self.simulator = ECGSimulator(mqtt_broker=mqtt_broker, mqtt_port=mqtt_port)
        
        # Create worker
        self.worker: Optional[SimulatorWorker] = None
        
        # State tracking
        self.state = "idle"
        self.current_patient: Optional[str] = None

        # Configurable chunk size (samples per MQTT message). This is forwarded to
        # the worker when a simulation is started. The worker enforces a minimum
        # of 1 sample but does not impose an explicit upper bound so we can
        # experimentally probe ESP32 limits.
        self.chunk_size_samples: int = SimulatorWorker.CHUNK_SIZE_SAMPLES
        
        logger.info("Controller initialized")
    
    def disconnect_mqtt(self) -> None:
        """Disconnect from MQTT broker."""
        if self.simulator:
            try:
                self.simulator.disconnect_mqtt()
                self.sig_mqtt_connected.emit(False)
                logger.info("MQTT disconnected via controller")
            except Exception as e:
                logger.error(f"Error disconnecting MQTT: {str(e)}")
    
    def initialize_mqtt(self) -> bool:
        """
        Initialize MQTT connection.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.simulator.connect_mqtt()
            self.sig_mqtt_connected.emit(True)
            self.state = "ready"
            self.sig_status_changed.emit("ready")
            logger.info("MQTT connection initialized")
            return True
            
        except Exception as e:
            logger.error(f"MQTT initialization failed: {str(e)}")
            self.sig_mqtt_connected.emit(False)
            self.state = "error"
            self.sig_status_changed.emit("error")
            return False
    
    def validate_patient(self, patient_id: str) -> bool:
        """
        Validate if patient exists in dataset.
        
        Args:
            patient_id: Patient identifier
            
        Returns:
            True if patient exists, False otherwise
        """
        return patient_id in self.simulator.patient_records
    
    def start_simulation(self, patient_id: str) -> bool:
        """
        Start ECG streaming simulation.
        Always uses 300 Hz sampling rate (fixed, no downsampling).
        
        If a simulation is already running, it will be aborted and a new one started.
        
        Args:
            patient_id: Patient identifier
            
        Returns:
            True if started successfully, False otherwise
        """
        try:
            # Validate patient
            if not self.validate_patient(patient_id):
                logger.error(f"Patient {patient_id} not found")
                return False
            
            # If a simulation is already running, abort it first
            if self.worker and self.worker.isRunning():
                logger.info("Aborting existing simulation to start new ECG transmission...")
                self.stop_simulation()
                # Wait a bit for the worker to stop
                if self.worker:
                    self.worker.wait(2000)
            
            # Update state
            self.state = "loading"
            self.sig_status_changed.emit("loading")
            
            # Create new worker
            self.worker = SimulatorWorker(self.simulator)
            
            # Set parameters (sampling rate is fixed at 300 Hz, chunk size configurable)
            self.worker.set_parameters(patient_id, self.chunk_size_samples)
            
            # Connect worker signals (controller level)
            self.worker.sig_connected.connect(self.on_worker_connected)
            self.worker.sig_error.connect(self.on_worker_error)
            self.worker.sig_finished.connect(self.on_worker_finished)
            
            # NOTE: Worker is created but NOT started yet
            # GUI should connect signals, then call start_worker()
            
            # Update state
            self.state = "ready_to_start"
            self.current_patient = patient_id
            
            logger.info(f"Simulation started for patient {patient_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start simulation: {str(e)}")
            self.state = "error"
            self.sig_status_changed.emit("error")
            return False
    
    def start_worker(self) -> bool:
        """
        Start the worker thread (call after connecting GUI signals).
        
        Returns:
            True if started successfully, False otherwise
        """
        if not self.worker:
            logger.error("No worker to start")
            return False
        
        try:
            # Start worker thread
            self.worker.start()
            
            # Update state
            self.state = "running"
            self.sig_status_changed.emit("running")
            
            logger.info(f"Worker thread started for patient {self.current_patient}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start worker: {str(e)}")
            self.state = "error"
            self.sig_status_changed.emit("error")
            return False
    
    def pause_simulation(self) -> None:
        """Pause the running simulation."""
        if self.worker:
            self.worker.pause()
            self.state = "paused"
            self.sig_status_changed.emit("paused")
            logger.info("Simulation paused")
    
    def resume_simulation(self) -> None:
        """Resume the paused simulation."""
        if self.worker:
            self.worker.resume()
            self.state = "running"
            self.sig_status_changed.emit("running")
            logger.info("Simulation resumed")
    
    def stop_simulation(self) -> None:
        """Stop the running simulation."""
        if self.worker:
            self.worker.stop()
            # Wait for thread to finish (with timeout)
            self.worker.wait(5000)
            self.state = "stopped"
            self.sig_status_changed.emit("stopped")
            logger.info("Simulation stopped")
    
    # Sampling rate is fixed at 300 Hz - no longer adjustable.
    # Chunk size (in samples) is configurable. We only enforce a minimum of 1
    # sample; there is intentionally no explicit upper bound here so we can
    # experimentally determine what the ESP32 + MQTT stack can handle.

    def set_chunk_size(self, chunk_size_samples: int) -> None:
        """
        Set the desired chunk size in samples (per MQTT message).
        """
        if chunk_size_samples < 1:
            chunk_size_samples = 1

        self.chunk_size_samples = int(chunk_size_samples)
        logger.info(f"Controller chunk size set to {self.chunk_size_samples} samples")
    
    def get_patient_list(self) -> List[str]:
        """
        Get list of all available patients.
        
        Returns:
            Sorted list of patient IDs
        """
        return self.simulator.list_patients()
    
    def get_patient_info(self, patient_id: str) -> Optional[Dict]:
        """
        Get detailed information about a patient.
        
        Args:
            patient_id: Patient identifier
            
        Returns:
            Dictionary with patient info or None if not found
        """
        return self.simulator.get_patient_info(patient_id)
    
    def on_worker_connected(self, msg: str) -> None:
        """
        Slot called when worker connects to MQTT.
        
        Args:
            msg: Connection status message
        """
        self.sig_mqtt_connected.emit(True)
        if self.state == "loading":
            self.state = "ready_to_stream"
        logger.info(f"Worker connected: {msg}")
    
    def on_worker_error(self, msg: str) -> None:
        """
        Slot called when worker encounters an error.
        
        Args:
            msg: Error message
        """
        logger.error(f"Worker error: {msg}")
        if self.state == "loading":
            self.state = "error"
            self.sig_status_changed.emit("error")
    
    def on_worker_finished(self) -> None:
        """Slot called when worker thread finishes."""
        self.state = "stopped"
        self.sig_status_changed.emit("stopped")
        
        # Disconnect worker signals
        if self.worker:
            try:
                self.worker.sig_connected.disconnect(self.on_worker_connected)
                self.worker.sig_error.disconnect(self.on_worker_error)
                self.worker.sig_finished.disconnect(self.on_worker_finished)
            except:
                pass
        
        logger.info("Worker finished")
    
    def cleanup(self) -> None:
        """Cleanup resources before shutdown."""
        if self.worker and self.worker.isRunning():
            self.stop_simulation()
        
        try:
            self.simulator.disconnect_mqtt()
        except:
            pass
        
        logger.info("Controller cleanup complete")

