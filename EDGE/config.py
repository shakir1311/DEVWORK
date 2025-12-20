"""
Configuration module for EDGE layer.
Centralizes all configuration settings.
"""

import os
from typing import Optional

# MQTT Configuration
MQTT_BROKER_PORT = 1885  # Different port from simulator broker (1883)
MQTT_CHUNK_TOPIC = "ecg/edge/chunk"      # Topic for receiving ECG chunks from ESP32
MQTT_ACK_TOPIC = "ecg/edge/ack"          # Topic for sending acknowledgments to ESP32
MQTT_COMMAND_TOPIC = "ecg/edge/command"  # Topic for sending commands to ESP32
MQTT_CLIENT_ID = "EDGE_ECG_Processor"

# Broker Discovery Configuration
BROKER_DISCOVERY_PORT = 1886  # Different port from simulator discovery (1884)
BROKER_DISCOVERY_MAGIC = b"ECG_MQTT_BROKER"
BROKER_DISCOVERY_RESPONSE_PREFIX = "ECG_MQTT_BROKER_RESPONSE"
BROKER_DISCOVERY_TIMEOUT = 5.0  # seconds

# ECG Data Configuration
ECG_SAMPLING_RATE = 300  # Hz - default sampling rate
CHUNK_HEADER_BYTES = 12  # Format: uint16 ver + uint16 fs + uint16 chunk_num + uint16 total_chunks + uint32 samples
PAYLOAD_FORMAT_VERSION = 3  # Expected format version

# Processing Configuration
ENABLE_AUTO_PROCESSING = True  # Automatically process ECG when all chunks received
PROCESSING_MODULES = [
    "heart_rate",    # Heart rate calculation
    "ml_inference",  # ML model classification
]

# ML Inference Configuration
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")  # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Data Storage Configuration
SAVE_RECEIVED_DATA = False  # Disabled - data is sent to Web Portal, not saved locally
DATA_STORAGE_DIR = "./data/received_ecg"  # Directory to save received ECG data

