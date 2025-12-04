"""
Chunk Receiver Module
Receives and assembles ECG chunks from ESP32.
"""

import struct
import logging
from typing import Optional, Dict, Callable
import numpy as np

from config import (
    CHUNK_HEADER_BYTES,
    PAYLOAD_FORMAT_VERSION,
    ECG_SAMPLING_RATE
)

logger = logging.getLogger(__name__)


class ChunkReceiver:
    """Receives and assembles ECG chunks into a complete record."""
    
    def __init__(self, on_complete_callback: Optional[Callable] = None):
        """
        Initialize chunk receiver.
        
        Args:
            on_complete_callback: Called when all chunks are received
                                 Signature: callback(ecg_data: np.ndarray, metadata: dict)
        """
        self.on_complete_callback = on_complete_callback
        
        # Chunk tracking
        self.total_chunks_expected = 0
        self.chunks_received: Dict[int, np.ndarray] = {}  # chunk_num -> chunk_data
        self.chunks_received_count = 0
        self.sampling_rate = ECG_SAMPLING_RATE
        self.chunk_size = 0  # Nominal chunk size (samples)
        
        # Metadata
        self.metadata: Dict = {}
        
        # Patient information (received from ESP32, excludes rhythm - determined by ML)
        self.patient_info: Dict = {}
    
    def process_chunk(self, payload: bytes) -> bool:
        """
        Process a received chunk.
        
        Args:
            payload: Raw chunk payload bytes
            
        Returns:
            True if chunk was processed successfully, False otherwise
        """
        if len(payload) < CHUNK_HEADER_BYTES:
            logger.error(f"Chunk payload too small: {len(payload)} bytes")
            return False
        
        try:
            # Parse header (little-endian)
            format_version = struct.unpack('<H', payload[0:2])[0]
            sampling_rate = struct.unpack('<H', payload[2:4])[0]
            chunk_num = struct.unpack('<H', payload[4:6])[0]
            total_chunks = struct.unpack('<H', payload[6:8])[0]
            sample_count = struct.unpack('<I', payload[8:12])[0]
            
            # Validate format version
            if format_version != PAYLOAD_FORMAT_VERSION:
                logger.error(f"Unsupported format version: {format_version} (expected {PAYLOAD_FORMAT_VERSION})")
                return False
            
            # Detect start of NEW ECG record (chunk 0) while we still have state from a previous record
            # This happens when ESP32 receives new data from simulator while EDGE is still receiving previous data
            if chunk_num == 0 and self.total_chunks_expected > 0:
                logger.warning("========================================")
                logger.warning("New ECG record detected (chunk 0) - resetting receiver state")
                logger.warning(f"Previous record: {self.chunks_received_count}/{self.total_chunks_expected} chunks received")
                logger.warning("========================================")
                # Reset state for new record
                self.total_chunks_expected = 0
                self.chunks_received.clear()
                self.chunks_received_count = 0
                self.chunk_size = 0
            
            # Initialize on first chunk (or after reset)
            if self.total_chunks_expected == 0:
                self.total_chunks_expected = total_chunks
                self.sampling_rate = sampling_rate if sampling_rate > 0 else ECG_SAMPLING_RATE
                self.chunk_size = sample_count  # Nominal chunk size from first chunk
                self.chunks_received.clear()
                self.chunks_received_count = 0
                
                logger.info("========================================")
                logger.info("Receiving ECG chunks from ESP32...")
                logger.info(f"Total chunks expected: {self.total_chunks_expected}")
                logger.info(f"Sampling rate: {self.sampling_rate} Hz")
                logger.info(f"Nominal chunk size: {self.chunk_size} samples")
                logger.info("========================================")
            
            # Validate chunk number
            if chunk_num >= self.total_chunks_expected:
                logger.error(f"Invalid chunk number: {chunk_num} (expected < {self.total_chunks_expected})")
                return False
            
            # Check for duplicate
            if chunk_num in self.chunks_received:
                logger.warning(f"Duplicate chunk {chunk_num} received")
                return True  # Still return True, we already have it
            
            # Parse chunk body (comma-separated float values)
            body_start = CHUNK_HEADER_BYTES
            body_str = payload[body_start:].decode('utf-8', errors='ignore')
            
            # Parse patient info from chunk 0 (if present)
            ecg_data_start = body_str
            if chunk_num == 0 and body_str.startswith("PATIENT_INFO:"):
                # Extract patient info line (ends with \n)
                newline_pos = body_str.find('\n')
                if newline_pos >= 0:
                    patient_info_str = body_str[:newline_pos]
                    ecg_data_start = body_str[newline_pos + 1:]  # ECG data starts after newline
                    self._parse_patient_info(patient_info_str)
            
            # Parse comma-separated values (skip patient info line if present)
            values = []
            for token in ecg_data_start.split(','):
                token = token.strip()
                if token:
                    try:
                        value = float(token)
                        values.append(value)
                    except ValueError:
                        logger.warning(f"Failed to parse value: {token}")
            
            if len(values) != sample_count:
                logger.error(f"Parsed {len(values)} values, expected {sample_count}")
                return False
            
            # Store chunk
            self.chunks_received[chunk_num] = np.array(values, dtype=np.float32)
            self.chunks_received_count += 1
            
            logger.debug(
                f"Received chunk {chunk_num + 1}/{self.total_chunks_expected} "
                f"({sample_count} samples, total={self.chunks_received_count}/{self.total_chunks_expected})"
            )
            
            # Check if all chunks received
            if self.chunks_received_count == self.total_chunks_expected:
                self._assemble_complete_record()
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing chunk: {e}", exc_info=True)
            return False
    
    def _assemble_complete_record(self):
        """Assemble all chunks into a complete ECG record."""
        logger.info("========================================")
        logger.info("✓ All chunks received! Assembling ECG record...")
        
        try:
            # Sort chunks by chunk number
            sorted_chunks = sorted(self.chunks_received.items())
            
            # Concatenate all chunks
            ecg_data = np.concatenate([chunk_data for _, chunk_data in sorted_chunks])
            
            total_samples = len(ecg_data)
            duration_seconds = total_samples / self.sampling_rate
            
            # Prepare metadata
            self.metadata = {
                'total_samples': total_samples,
                'sampling_rate': self.sampling_rate,
                'duration_seconds': duration_seconds,
                'total_chunks': self.total_chunks_expected,
                'chunk_size': self.chunk_size,
                'min_value': float(np.min(ecg_data)),
                'max_value': float(np.max(ecg_data)),
                'mean_value': float(np.mean(ecg_data)),
            }
            
            # Add patient info to metadata (if available)
            if self.patient_info:
                self.metadata['patient_info'] = self.patient_info.copy()
            
            logger.info(f"Assembled ECG record: {total_samples} samples, {duration_seconds:.2f}s @ {self.sampling_rate}Hz")
            logger.info(f"Value range: [{self.metadata['min_value']:.3f}, {self.metadata['max_value']:.3f}] mV")
            logger.info("========================================")
            
            # Call completion callback
            if self.on_complete_callback:
                self.on_complete_callback(ecg_data, self.metadata)
            
        except Exception as e:
            logger.error(f"Error assembling ECG record: {e}", exc_info=True)
    
    def _parse_patient_info(self, info_str: str):
        """
        Parse patient information from chunk 0.
        
        Format: "PATIENT_INFO:patient_id|DURATION:duration|SAMPLES:samples|DATE:date|TIME:time"
        Example: "PATIENT_INFO:A00001|DURATION:30.00|SAMPLES:9000|DATE:2024-01-01|TIME:12:00:00"
        
        Note: Rhythm information is NOT included (will be determined by ML on EDGE).
        """
        if not info_str.startswith("PATIENT_INFO:"):
            return
        
        # Clear previous patient info
        self.patient_info.clear()
        
        # Skip "PATIENT_INFO:" prefix
        data = info_str[13:]
        
        # Parse fields separated by |
        fields = data.split('|')
        is_first_field = True
        
        for field in fields:
            if is_first_field:
                # First field after "PATIENT_INFO:" is the patient ID (no prefix)
                self.patient_info['patient_id'] = field
                is_first_field = False
            elif field.startswith("DURATION:"):
                try:
                    self.patient_info['duration_seconds'] = float(field[9:])
                except ValueError:
                    logger.warning(f"Failed to parse duration: {field}")
            elif field.startswith("SAMPLES:"):
                try:
                    self.patient_info['total_samples'] = int(field[8:])
                except ValueError:
                    logger.warning(f"Failed to parse samples: {field}")
            elif field.startswith("DATE:"):
                self.patient_info['record_date'] = field[5:]
            elif field.startswith("TIME:"):
                self.patient_info['record_time'] = field[5:]
        
        # Log parsed patient info
        logger.info("========================================")
        logger.info("Patient information received:")
        logger.info(f"  Patient ID: {self.patient_info.get('patient_id', 'N/A')}")
        logger.info(f"  Duration: {self.patient_info.get('duration_seconds', 0):.2f} seconds")
        logger.info(f"  Total Samples: {self.patient_info.get('total_samples', 0)}")
        if 'record_date' in self.patient_info:
            logger.info(f"  Date: {self.patient_info['record_date']}")
        if 'record_time' in self.patient_info:
            logger.info(f"  Time: {self.patient_info['record_time']}")
        logger.info("========================================")
    
    def reset(self):
        """Reset receiver state (for new ECG record)."""
        self.total_chunks_expected = 0
        self.chunks_received.clear()
        self.chunks_received_count = 0
        self.chunk_size = 0
        self.metadata.clear()
        self.patient_info.clear()
        logger.debug("Chunk receiver reset")

