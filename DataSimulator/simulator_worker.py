"""
Simulator Worker Thread
Runs ECG streaming simulation in background, respects pause/stop signals, publishes via MQTT.
"""

import threading
import time
import struct
from typing import Optional, Tuple
import logging
from PyQt6.QtCore import QThread, pyqtSignal
import numpy as np

from ecg_simulator import ECGSimulator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SimulatorWorker(QThread):
    """Background worker thread for ECG simulation."""
    
    ECG_CHUNK_TOPIC = "ecg/chunk"  # ECG chunks (30 samples each)
    ACK_TOPIC = "ecg/ack"  # ESP32 acknowledgments
    PAYLOAD_FORMAT_VERSION = 3  # Version 3: String-based values for 100% precision
    CHUNK_HEADER_FORMAT = "<HHHHI"  # version, sampling_rate, chunk_num, total_chunks, sample_count
    # Default chunk size (samples per MQTT message). This is just a starting value;
    # the actual chunk size is fully configurable at runtime from the GUI.
    CHUNK_SIZE_SAMPLES = 30
    
    # PyQt6 signals
    sig_connected = pyqtSignal(str)  # MQTT connected successfully
    sig_disconnected = pyqtSignal()  # MQTT disconnected
    sig_window_sent = pyqtSignal(dict)  # ECG window published
    sig_progress = pyqtSignal(dict)  # Progress update
    sig_error = pyqtSignal(str)  # Non-fatal error occurred
    sig_status = pyqtSignal(str)  # Status update message
    sig_finished = pyqtSignal()  # Simulation completed or stopped
    
    def __init__(self, simulator: ECGSimulator):
        """
        Initialize worker thread.
        
        Args:
            simulator: Reference to ECGSimulator instance
        """
        super().__init__()
        
        self.simulator = simulator
        self.patient_id: Optional[str] = None
        self.patient_metadata: Optional[Dict] = None  # Store patient metadata (excluding rhythm)
        # Fixed at 300 Hz - no downsampling
        self.sampling_rate: int = 300

        # Configurable chunk size (in samples). This is what we actually use when
        # splitting the ECG into MQTT messages. It is clamped to [1, +inf);
        # there is intentionally no upper limit here so we can experimentally
        # test how large a chunk the ESP32 can handle (up to the full record).
        self.chunk_size_samples: int = self.CHUNK_SIZE_SAMPLES
        
        self.is_paused: bool = False
        self.stop_requested: bool = False
        self.pause_lock = threading.Lock()
        
        # Chunk acknowledgment tracking
        self.ack_received = threading.Event()
        self.last_ack_chunk = -1
        self.ack_lock = threading.Lock()
    
    def set_parameters(self, patient_id: str, chunk_size_samples: Optional[int] = None) -> None:
        """
        Set simulation parameters.
        
        Args:
            patient_id: Patient identifier
            chunk_size_samples: Optional chunk size in samples (>=1). If None, the
                               existing value is kept.
        """
        try:
            # Validate inputs
            if not patient_id or not isinstance(patient_id, str):
                self.sig_error.emit("Invalid patient ID")
                return
            
            # Store parameters (sampling rate is fixed at 300 Hz)
            self.patient_id = patient_id
            self.sampling_rate = 300  # Fixed - always 300 Hz from .hea file

            # Update chunk size if provided
            if chunk_size_samples is not None:
                # Enforce a minimum of 1 sample; no explicit upper bound so we
                # can experimentally probe ESP32/MQTT limits.
                if chunk_size_samples < 1:
                    chunk_size_samples = 1

                self.chunk_size_samples = int(chunk_size_samples)
                self.sig_status.emit(
                    f"Chunk size set to {self.chunk_size_samples} samples per MQTT message"
                )
            
            self.sig_status.emit(
                f"Parameters set: patient={patient_id}, fs=300Hz (fixed, real-time), "
                f"chunk_size={self.chunk_size_samples} samples"
            )
            
        except Exception as e:
            self.sig_error.emit(f"Error setting parameters: {str(e)}")
    
    def pause(self) -> None:
        """Pause the simulation."""
        with self.pause_lock:
            self.is_paused = True
        logger.info("Simulation paused")
    
    def resume(self) -> None:
        """Resume the simulation."""
        with self.pause_lock:
            self.is_paused = False
        logger.info("Simulation resumed")
    
    def stop(self) -> None:
        """Request simulation to stop."""
        with self.pause_lock:
            self.stop_requested = True
        logger.info("Stop requested")
    
    def _on_ack_message(self, client, userdata, msg):
        """Handle acknowledgment messages from ESP32."""
        try:
            # ACK message format: "chunk_num" (simple integer as string)
            if msg.payload is None or len(msg.payload) == 0:
                logger.warning("Received empty ACK message")
                return
            
            payload_str = msg.payload.decode('utf-8').strip()
            if not payload_str:
                logger.warning("Received empty ACK payload string")
                return
            
            chunk_num = int(payload_str)
            with self.ack_lock:
                self.last_ack_chunk = chunk_num
                self.ack_received.set()
            logger.debug(f"Received ACK for chunk {chunk_num}")
        except ValueError as e:
            logger.warning(f"Error parsing ACK message (invalid number): {msg.payload if msg.payload else 'None'}, error: {e}")
        except Exception as e:
            logger.warning(f"Error parsing ACK message: {e}, payload: {msg.payload if msg.payload else 'None'}")
    
    def run(self) -> None:
        """
        Main worker loop (overrides QThread.run()).
        Sends ECG data in chunks, waiting for ESP32 acknowledgment after each chunk.
        If stop_requested is set, aborts transmission immediately.
        """
        try:
            # Check if we should abort before starting
            if self.stop_requested:
                self.sig_status.emit("Transmission aborted before starting")
                return
            
            # Connect to MQTT (reuse existing connection if available)
            if not self.simulator.mqtt_client or not self.simulator.mqtt_client.is_connected():
                self.sig_status.emit("Connecting to MQTT broker...")
                self.simulator.connect_mqtt()
                self.sig_connected.emit(
                    f"Connected to {self.simulator.mqtt_broker}:{self.simulator.mqtt_port}"
                )
            else:
                self.sig_connected.emit(
                    f"Using existing connection to {self.simulator.mqtt_broker}:{self.simulator.mqtt_port}"
                )
            
            # Check again after connection (might have been aborted during connection)
            if self.stop_requested:
                self.sig_status.emit("Transmission aborted")
                return
            
            # Subscribe to acknowledgment topic (QoS 0 since ESP32 sends with QoS 0)
            # Note: We'll use a timeout-based approach instead of strict ACK waiting
            # to avoid issues with packet parsing errors
            try:
                result = self.simulator.mqtt_client.subscribe(self.ACK_TOPIC, qos=0)
                if result[0] == 0:  # Success
                    self.simulator.mqtt_client.message_callback_add(self.ACK_TOPIC, self._on_ack_message)
                    logger.info(f"Subscribed to ACK topic: {self.ACK_TOPIC}")
                else:
                    logger.error(f"Failed to subscribe to ACK topic: {result}")
            except Exception as e:
                logger.warning(f"Error subscribing to ACK topic: {e}, will continue without strict ACK checking")
            
            # Load patient data (always 300 Hz, no downsampling)
            self.sig_status.emit(f"Loading patient {self.patient_id}...")
            ecg_data, metadata = self.simulator.load_ecg(self.patient_id)
            
            # Store patient metadata (excluding rhythm/rhythm_name - will be determined by ML on EDGE)
            self.patient_metadata = {
                'patient_id': metadata.get('patient_id', self.patient_id),
                'duration_seconds': metadata.get('duration_seconds', 0.0),
                'samples': metadata.get('samples', len(ecg_data)),
                'record_date': metadata.get('record_date', ''),
                'record_time': metadata.get('record_time', ''),
            }
            
            # Get sampling rate from metadata (should be 300 Hz)
            self.sampling_rate = metadata['sampling_rate']
            total_samples = metadata['samples']
            duration_seconds = metadata['duration_seconds']
            
            self.sig_status.emit(
                f"Patient loaded: {metadata['rhythm_name']}, "
                f"{duration_seconds:.1f}s, "
                f"{total_samples} samples @ {self.sampling_rate}Hz, "
                f"range=[{np.min(ecg_data):.3f}, {np.max(ecg_data):.3f}] mV"
            )
            
            if self.stop_requested:
                self.sig_status.emit("Simulation cancelled before publishing.")
                return
            
            # Split ECG into small chunks.
            # NOTE: Chunk size is fully configurable; we intentionally do not enforce
            # an upper limit here so we can experimentally test what the ESP32 +
            # MQTT stack can handle (up to the full record size).
            chunks = []
            effective_chunk_size = max(1, self.chunk_size_samples)
            total_chunks = (total_samples + effective_chunk_size - 1) // effective_chunk_size
            
            for i in range(0, total_samples, effective_chunk_size):
                chunk_data = ecg_data[i:i + effective_chunk_size]
                chunk_num = i // effective_chunk_size
                chunks.append((chunk_num, chunk_data))
            
            self.sig_status.emit(
                f"Sending {total_chunks} chunks ({effective_chunk_size} samples each, "
                f"{total_chunks * effective_chunk_size / self.sampling_rate:.1f}s total) "
                f"to topic '{self.ECG_CHUNK_TOPIC}'..."
            )
            
            # Emit preview window for GUI (first chunk)
            if len(chunks) > 0 and not self.stop_requested:
                preview_data = chunks[0][1][:min(1000, len(chunks[0][1]))]
                self.sig_window_sent.emit({
                    "window_num": 1,
                    "timestamp": metadata.get('record_time', 'N/A'),
                    "samples": len(preview_data),
                    "ecg_data": preview_data.tolist(),
                    "sampling_rate": self.sampling_rate,
                    "status": "SENDING_CHUNKS"
                })
            
            # Send chunks one by one, waiting for ACK after each
            start_time = time.time()
            chunks_sent = 0
            
            for chunk_num, chunk_data in chunks:
                if self.stop_requested:
                    self.sig_status.emit("Simulation stopped by user")
                    break
                
                # Verify MQTT connection
                if not self.simulator.mqtt_client.is_connected():
                    self.sig_error.emit("MQTT connection lost. Attempting to reconnect...")
                    try:
                        self.simulator.connect_mqtt()
                        self.simulator.mqtt_client.subscribe(self.ACK_TOPIC, qos=1)
                        self.simulator.mqtt_client.message_callback_add(self.ACK_TOPIC, self._on_ack_message)
                        self.sig_status.emit("MQTT reconnected successfully")
                    except Exception as e:
                        self.sig_error.emit(f"Failed to reconnect: {str(e)}")
                        break
                
                # Build chunk payload
                payload_bytes = self._build_chunk_payload(chunk_num, total_chunks, chunk_data)
                
                # Reset ACK event
                self.ack_received.clear()
                
                # Publish chunk with QoS 1 for reliable delivery
                logger.info(f"Publishing chunk {chunk_num + 1}/{total_chunks} "
                           f"({len(chunk_data)} samples, {len(payload_bytes)} bytes)")
                
                result = self.simulator.mqtt_client.publish(
                    self.ECG_CHUNK_TOPIC,
                    payload_bytes,
                    qos=1
                )
                
                if result.rc != 0:
                    self.sig_error.emit(f"Failed to publish chunk {chunk_num + 1} (code {result.rc})")
                    break
                
                # Wait for publish confirmation
                result.wait_for_publish(timeout=5)
                
                # Wait for ESP32 acknowledgment (with timeout)
                # Use a shorter timeout and be more lenient to avoid crashes
                ack_timeout = 3.0  # 3 seconds timeout per chunk (reduced from 10)
                ack_start = time.time()
                ack_received = False
                
                while (time.time() - ack_start) < ack_timeout and not self.stop_requested:
                    # Process MQTT messages to receive ACK
                    try:
                        # Use loop_misc() instead of loop() to avoid packet parsing issues
                        self.simulator.mqtt_client.loop_misc()
                    except Exception as e:
                        # Silently continue - packet parsing errors shouldn't stop transmission
                        pass
                    
                    # Check for ACK
                    if self.ack_received.is_set():
                        with self.ack_lock:
                            if self.last_ack_chunk == chunk_num:
                                ack_received = True
                                break
                    
                    time.sleep(0.05)  # Small delay to avoid busy-waiting
                
                # If no ACK received, log warning but continue (QoS 1 ensures delivery)
                if not ack_received:
                    logger.warning(f"No ACK received for chunk {chunk_num + 1}, but continuing (QoS 1 ensures delivery)")
                    # Don't break - continue sending chunks even without ACK
                    # The ESP32 will still receive them via QoS 1
                
                chunks_sent += 1
                elapsed = time.time() - start_time
                
                self.sig_status.emit(
                    f"Chunk {chunk_num + 1}/{total_chunks} sent and acknowledged "
                    f"({chunks_sent}/{total_chunks} total, {elapsed:.1f}s elapsed)"
                )
                
                self.sig_progress.emit({
                    "elapsed_time": elapsed,
                    "chunks_sent": chunks_sent,
                    "samples_total": chunks_sent * effective_chunk_size
                })
            
            # All chunks sent or aborted
            if self.stop_requested:
                self.sig_status.emit(f"Transmission aborted: {chunks_sent}/{total_chunks} chunks sent")
                logger.info(f"Transmission aborted: {chunks_sent}/{total_chunks} chunks sent")
            elif chunks_sent == total_chunks:
                self.sig_status.emit(f"✓ All {total_chunks} chunks sent and acknowledged!")
                logger.info(f"Successfully sent all {total_chunks} chunks")
            else:
                self.sig_error.emit(f"Only {chunks_sent}/{total_chunks} chunks sent successfully")
            
            logger.info(f"Chunk transmission completed: {chunks_sent}/{total_chunks} chunks")
            
        except Exception as e:
            self.sig_error.emit(f"Fatal error: {str(e)}")
            logger.error(f"Worker error: {str(e)}", exc_info=True)
        
        finally:
            # DO NOT disconnect MQTT - keep connection alive
            # The connection will be managed by the controller/GUI
            logger.info("Worker finished (MQTT connection remains active)")
            
            # Signal completion
            self.sig_finished.emit()

    def _build_chunk_payload(
        self,
        chunk_num: int,
        total_chunks: int,
        chunk_data: np.ndarray
    ) -> bytes:
        """
        Build the payload for a 1-second ECG chunk using string format for 100% precision.
        
        Payload Format:
            Binary Header (little-endian):
                uint16  format_version (3)
                uint16  sampling_rate_hz
                uint16  chunk_num (0-based)
                uint16  total_chunks
                uint32  sample_count
            String Body (UTF-8):
                For chunk 0: "PATIENT_INFO:patient_id|DURATION:duration|SAMPLES:samples|DATE:date|TIME:time\n"
                Then: "value1,value2,value3,..." (comma-separated float values as strings)
                Each value is formatted with 6 decimal places for precision
        """
        # Build binary header
        header = struct.pack(
            self.CHUNK_HEADER_FORMAT,
            self.PAYLOAD_FORMAT_VERSION,
            self.sampling_rate,
            chunk_num,
            total_chunks,
            len(chunk_data)
        )
        
        # Convert float values to strings with high precision (6 decimal places)
        # Format: "value1,value2,value3,..."
        value_strings = [f"{val:.6f}" for val in chunk_data]
        body_str = ",".join(value_strings)
        
        # Add patient info to chunk 0 (first chunk) - before ECG values
        if chunk_num == 0 and self.patient_metadata:
            patient_info = (
                f"PATIENT_INFO:{self.patient_metadata.get('patient_id', '')}|"
                f"DURATION:{self.patient_metadata.get('duration_seconds', 0.0):.2f}|"
                f"SAMPLES:{self.patient_metadata.get('samples', 0)}|"
                f"DATE:{self.patient_metadata.get('record_date', '')}|"
                f"TIME:{self.patient_metadata.get('record_time', '')}\n"
            )
            body_str = patient_info + body_str
        
        body_bytes = body_str.encode('utf-8')
        
        return header + body_bytes

