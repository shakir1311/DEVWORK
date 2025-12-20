"""
EDGE Layer Main Entry Point
Receives ECG data from ESP32 via MQTT and processes it.
"""

import logging
import signal
import sys
import time
import threading
from typing import Optional
import requests
import json

from config import (
    LOG_LEVEL,
    LOG_FORMAT,
    ENABLE_AUTO_PROCESSING,
    PROCESSING_MODULES,
    MODELS_DIR
)

from mqtt_client import MQTTClient

PORTAL_API_URL = "http://localhost:8000/api/ingest"
from mqtt_broker import EDGEMQTTBroker
from chunk_receiver import ChunkReceiver
from ecg_processor import ProcessorPipeline
from data_storage import DataStorage
from processors import HeartRateProcessor, MLInferenceProcessor

# GUI imports (optional)
try:
    from PyQt6.QtWidgets import QApplication
    from edge_gui import EDGEGUI
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    logger.warning("PyQt6 not available - GUI disabled")

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format=LOG_FORMAT
)
logger = logging.getLogger(__name__)


class EDGELayer:
    """Main EDGE layer application."""
    
    def __init__(self, gui: Optional['EDGEGUI'] = None):
        """
        Initialize EDGE layer.
        
        Args:
            gui: Optional GUI instance for visualization
        """
        self.running = False
        self.mqtt_broker: Optional[EDGEMQTTBroker] = None
        self.mqtt_client: Optional[MQTTClient] = None
        self.chunk_receiver: Optional[ChunkReceiver] = None
        self.processor_pipeline: Optional[ProcessorPipeline] = None
        self.data_storage: Optional[DataStorage] = None
        self.gui = gui
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
        sys.exit(0)
    
    def initialize(self):
        """Initialize all components."""
        logger.info("========================================")
        logger.info("EDGE Layer - ECG Data Processor")
        logger.info("========================================")
        
        # Initialize data storage
        self.data_storage = DataStorage()
        
        # Initialize processor pipeline
        self.processor_pipeline = ProcessorPipeline()
        
        # Add processors based on configuration
        if 'heart_rate' in PROCESSING_MODULES:
            self.processor_pipeline.add_processor(HeartRateProcessor())
        
        if 'ml_inference' in PROCESSING_MODULES:
            self.ml_processor = MLInferenceProcessor(models_dir=MODELS_DIR)
            self.processor_pipeline.add_processor(self.ml_processor)
            
            # Auto-load preferred model (ECG-DualNet preferred, fallback to first)
            available = self.ml_processor.get_available_models()
            if available:
                # Prefer ECG-DualNet if available (check for 'ECG-DualNet' in name)
                selected_model = available[0]  # Default to first
                for model in available:
                    fname = model.get('filename', '').lower()
                    mtype = model.get('type', '')
                    if 'ecg-dualnet' in fname or 'dualnet' in fname or mtype == 'ecg_dualnet':
                        selected_model = model
                        break
                
                self.ml_processor.load_model(selected_model['path'])
                logger.info(f"Auto-loaded model: {selected_model['filename']}")
            else:
                logger.info("No models found in models directory - copy trained models to use inference")
        
        # Initialize chunk receiver with callback
        self.chunk_receiver = ChunkReceiver(on_complete_callback=self._on_ecg_complete)
        
        # Initialize MQTT client with message callback
        self.mqtt_client = MQTTClient(on_message_callback=self._on_mqtt_message)
        
        # Connect GUI signals if available
        if self.gui:
            self.gui.get_signal_emitter().request_ecg_data.connect(self._on_request_ecg_data)
            self.gui.get_signal_emitter().model_changed.connect(self._on_model_changed)
            
            # Populate model dropdown
            if hasattr(self, 'ml_processor'):
                self.gui.update_models_list(self.ml_processor.get_available_models())
        
        logger.info("✓ Components initialized")
    
    def _on_mqtt_message(self, topic: str, payload: bytes):
        """
        Handle incoming MQTT message.
        
        Args:
            topic: MQTT topic
            payload: Message payload bytes
        """
        # DEBUG: Confirm callback is being triggered
        logger.info(f"[DEBUG] Received MQTT message on topic: {topic}, payload size: {len(payload)} bytes")
        
        from config import MQTT_CHUNK_TOPIC
        if topic == MQTT_CHUNK_TOPIC:
            # Process chunk
            success = self.chunk_receiver.process_chunk(payload)
            
            if success:
                # Extract chunk number from payload for ACK
                try:
                    import struct
                    chunk_num = struct.unpack('<H', payload[4:6])[0]
                    # Send ACK
                    self.mqtt_client.send_ack(chunk_num)
                except Exception as e:
                    logger.warning(f"Failed to send ACK: {e}")
        else:
            logger.warning(f"Received message on unexpected topic: {topic}")
    
    def _on_request_ecg_data(self):
        """Handle request for ECG data from GUI (thread-safe via signal)."""
        if self.mqtt_client and self.mqtt_client.connected:
            logger.info("GUI requested ECG data - sending TRANSMIT command to ESP32")
            if self.mqtt_client.send_command("TRANSMIT"):
                logger.info("✓ Transmission request sent to ESP32")
                if self.gui:
                    self.gui.get_signal_emitter().log_message_signal.emit("ECG data request sent to ESP32", "success")
            else:
                logger.warning("Failed to send transmission request to ESP32")
                if self.gui:
                    self.gui.get_signal_emitter().log_message_signal.emit("Failed to send ECG data request", "error")
        else:
            logger.warning("Cannot request ECG data: MQTT client not connected")
            if self.gui:
                self.gui.get_signal_emitter().log_message_signal.emit("Cannot request ECG data: not connected to MQTT broker", "error")
    
    def _on_model_changed(self, model_path: str):
        """Handle model selection change from GUI."""
        if not hasattr(self, 'ml_processor'):
            return
        
        # Handle refresh request
        if model_path == "__REFRESH__":
            self.ml_processor.refresh_models()
            if self.gui:
                self.gui.update_models_list(self.ml_processor.get_available_models())
            return
        
        # Load the selected model
        if self.ml_processor.load_model(model_path):
            logger.info(f"Model loaded successfully: {model_path}")
            if self.gui:
                self.gui.get_signal_emitter().log_message_signal.emit(
                    f"Model loaded: {self.ml_processor.current_model_type}", "success"
                )
        else:
            logger.error(f"Failed to load model: {model_path}")
            if self.gui:
                self.gui.get_signal_emitter().log_message_signal.emit(
                    f"Failed to load model", "error"
                )
    
    
    def _send_to_portal(self, ecg_data, metadata, results):
        """Send data to the Doctor's Portal API."""
        try:
            # Convert NumPy array to list for JSON serialization
            ecg_list = ecg_data.tolist() if hasattr(ecg_data, 'tolist') else list(ecg_data)
            
            payload = {
                "ecg_values": ecg_list,
                "metadata": metadata,
                "results": results
            }
            
            headers = {'Content-Type': 'application/json'}
            # Use short timeout to avoid blocking main thread too long
            response = requests.post(PORTAL_API_URL, json=payload, headers=headers, timeout=2)
            
            if response.status_code == 200:
                logger.info(f"✓ Data sent to Portal (ID: {response.json().get('record_id')})")
            else:
                logger.warning(f"Failed to send to Portal: {response.status_code} {response.text}")
                
        except requests.exceptions.ConnectionError:
            logger.warning("Portal unreachable (Connection refused). Is the web server running?")
        except Exception as e:
            logger.error(f"Error sending to Portal: {e}")

    def _on_ecg_complete(self, ecg_data, metadata: dict):
        """
        Handle complete ECG record received.
        
        Args:
            ecg_data: Complete ECG signal array
            metadata: Metadata dictionary
        """
        logger.info("========================================")
        logger.info("Complete ECG record received!")
        logger.info(f"Samples: {metadata['total_samples']}")
        logger.info(f"Duration: {metadata['duration_seconds']:.2f}s")
        logger.info(f"Sampling rate: {metadata['sampling_rate']} Hz")
        logger.info("========================================")
        
        # Process ECG data if enabled
        processing_results = None
        if ENABLE_AUTO_PROCESSING and self.processor_pipeline:
            logger.info("Running ECG processing pipeline...")
            processing_results = self.processor_pipeline.process(ecg_data, metadata)
            logger.info(f"Processing complete. Processors run: {processing_results['processors_run']}")
        
        # Update GUI if available (use signals for thread-safe updates)
        if self.gui:
            try:
                # Emit signal for thread-safe GUI update
                signal_emitter = self.gui.get_signal_emitter()
                signal_emitter.ecg_data_received.emit(ecg_data, metadata)
                
                # Update processing results via signal (include metadata for Patient ID)
                if processing_results:
                    processing_results['metadata'] = metadata
                    signal_emitter.processing_results_updated.emit(processing_results)
                
                logger.info("GUI updated with new ECG data")
            except Exception as e:
                logger.error(f"Error updating GUI: {e}", exc_info=True)
        
        # Send to Doctor's Portal
        if processing_results:
            self._send_to_portal(ecg_data, metadata, processing_results)
        
        # Save data if enabled
        if self.data_storage:
            self.data_storage.save_ecg_data(ecg_data, metadata, processing_results)
        
        # Reset receiver for next record
        self.chunk_receiver.reset()
        
        # Note: User can manually request next ECG data using the "Fetch ECG Data" button
        # This gives user control over when to request new data
    
    def start(self, broker_ip: Optional[str] = None, broker_port: Optional[int] = None, 
              start_broker: bool = True):
        """
        Start the EDGE layer.
        
        Args:
            broker_ip: Optional broker IP (will start embedded broker if None and start_broker=True)
            broker_port: Optional broker port (will use default if None)
            start_broker: If True, start embedded MQTT broker (default: True)
        """
        self.running = True
        
        # Start embedded MQTT broker if requested
        if start_broker:
            logger.info("Starting embedded MQTT broker...")
            from config import MQTT_BROKER_PORT
            self.mqtt_broker = EDGEMQTTBroker(host="0.0.0.0", port=broker_port or MQTT_BROKER_PORT)
            if not self.mqtt_broker.start():
                logger.error("Failed to start embedded MQTT broker")
                return
            
            # Get broker IP for client connection
            import socket
            try:
                # Get local IP address
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                broker_ip = s.getsockname()[0]
                s.close()
            except:
                broker_ip = "127.0.0.1"
            
            logger.info(f"Embedded broker running, connecting client to {broker_ip}")
        
        # Connect MQTT client
        if broker_ip:
            from config import MQTT_BROKER_PORT
            logger.info(f"Connecting to MQTT broker: {broker_ip}:{broker_port or MQTT_BROKER_PORT}")
            if not self.mqtt_client.connect(broker_ip, broker_port):
                logger.error("Failed to connect to MQTT broker")
                return
        else:
            logger.info("Discovering and connecting to MQTT broker...")
            if not self.mqtt_client.discover_and_connect():
                logger.error("Failed to discover and connect to MQTT broker")
                return
        
        # Update GUI connection status (use signal for thread-safe update)
        if self.gui:
            signal_emitter = self.gui.get_signal_emitter()
            signal_emitter.connection_status_changed.emit(True)
            signal_emitter.log_message_signal.emit("Connected to MQTT broker", "success")
        
        logger.info("✓ EDGE layer started and ready to receive ECG data")
        logger.info("Use the 'Fetch ECG Data' button in the GUI to request ECG data from ESP32")
        logger.info("Press Ctrl+C to stop")
        
        # Main loop
        try:
            while self.running:
                # Maintain MQTT connection
                connected = self.mqtt_client.connected
                self.mqtt_client.maintain_connection()
                
                # Update GUI connection status if changed (use signal for thread-safe update)
                if self.gui and connected != self.mqtt_client.connected:
                    self.gui.get_signal_emitter().connection_status_changed.emit(self.mqtt_client.connected)
                
                # Process GUI events if GUI is available
                if self.gui and GUI_AVAILABLE:
                    from PyQt6.QtWidgets import QApplication
                    QApplication.processEvents()
                
                time.sleep(0.1)  # Shorter sleep for GUI responsiveness
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the EDGE layer."""
        logger.info("Stopping EDGE layer...")
        self.running = False
        
        if self.gui:
            signal_emitter = self.gui.get_signal_emitter()
            signal_emitter.connection_status_changed.emit(False)
            signal_emitter.log_message_signal.emit("EDGE layer stopping...", "warning")
        
        if self.mqtt_broker:
            self.mqtt_broker.stop()
        
        if self.mqtt_client:
            self.mqtt_client.disconnect()
        
        logger.info("✓ EDGE layer stopped")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="EDGE Layer - ECG Data Processor")
    parser.add_argument(
        '--broker-ip',
        type=str,
        help='MQTT broker IP address (will discover if not provided)'
    )
    parser.add_argument(
        '--broker-port',
        type=int,
        default=None,
        help='MQTT broker port (default: 1885 from config)'
    )
    parser.add_argument(
        '--no-broker',
        action='store_true',
        help='Do not start embedded broker (connect to external broker)'
    )
    parser.add_argument(
        '--no-gui',
        action='store_true',
        help='Disable GUI (run in console mode)'
    )
    parser.add_argument(
        '--log-level',
        type=str,
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default=LOG_LEVEL,
        help='Logging level (default: INFO)'
    )
    
    args = parser.parse_args()
    
    # Update log level if specified
    if args.log_level:
        logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))
    
    # Initialize GUI if available and not disabled
    gui = None
    app = None
    if not args.no_gui and GUI_AVAILABLE:
        from PyQt6.QtWidgets import QApplication
        app = QApplication(sys.argv)
        gui = EDGEGUI()
        gui.show()
        logger.info("GUI started")
    elif args.no_gui:
        logger.info("GUI disabled by --no-gui flag")
    else:
        logger.warning("GUI not available (PyQt6 not installed)")
    
    # Create and start EDGE layer
    edge = EDGELayer(gui=gui)
    edge.initialize()
    
    # Start EDGE layer in a separate thread if GUI is running
    if gui:
        import threading
        edge_thread = threading.Thread(target=lambda: edge.start(
            broker_ip=args.broker_ip,
            broker_port=args.broker_port,
            start_broker=not args.no_broker
        ), daemon=True)
        edge_thread.start()
        
        # Run GUI event loop
        try:
            sys.exit(app.exec())
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            edge.stop()
    else:
        # Run in console mode
        edge.start(
            broker_ip=args.broker_ip,
            broker_port=args.broker_port,
            start_broker=not args.no_broker
        )


if __name__ == "__main__":
    main()

