"""
WFDB Header (.hea) File Parser
Parses PhysioNet WFDB header files to extract recording metadata.
"""

import os
import logging
from typing import Optional, Dict
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class HEAMetadata:
    """Metadata extracted from .hea file."""
    record_name: str
    num_signals: int
    sampling_frequency: int  # Hz
    num_samples: int
    duration_seconds: float
    time: Optional[str] = None  # Recording time (HH:MM:SS)
    date: Optional[str] = None  # Recording date (DD/MM/YYYY)
    signal_file: Optional[str] = None
    format: Optional[str] = None  # e.g., "16+24"
    adc_units: Optional[str] = None  # e.g., "1000/mV"
    adc_resolution: Optional[int] = None  # bits
    adc_zero: Optional[int] = None
    initial_value: Optional[int] = None
    description: Optional[str] = None  # e.g., "ECG"


class HEAParser:
    """Parser for WFDB header (.hea) files."""
    
    @staticmethod
    def parse_hea_file(hea_file_path: str) -> Optional[HEAMetadata]:
        """
        Parse a .hea file and extract metadata.
        
        Args:
            hea_file_path: Path to .hea file
            
        Returns:
            HEAMetadata object or None if parsing fails
        """
        try:
            if not os.path.exists(hea_file_path):
                logger.warning(f".hea file not found: {hea_file_path}")
                return None
            
            with open(hea_file_path, 'r') as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            
            if len(lines) < 1:
                logger.error(f"Empty .hea file: {hea_file_path}")
                return None
            
            # Parse first line (record header)
            # Format: record_name num_signals sampling_freq num_samples time date
            header_parts = lines[0].split()
            if len(header_parts) < 4:
                logger.error(f"Invalid header line in {hea_file_path}: {lines[0]}")
                return None
            
            record_name = header_parts[0]
            num_signals = int(header_parts[1])
            sampling_frequency = int(header_parts[2])
            num_samples = int(header_parts[3])
            duration_seconds = num_samples / sampling_frequency if sampling_frequency > 0 else 0.0
            
            time = header_parts[4] if len(header_parts) > 4 else None
            date = header_parts[5] if len(header_parts) > 5 else None
            
            # Parse second line (signal description) if available
            signal_file = None
            format_str = None
            adc_units = None
            adc_resolution = None
            adc_zero = None
            initial_value = None
            description = None
            
            if len(lines) > 1:
                signal_parts = lines[1].split()
                if len(signal_parts) >= 9:
                    signal_file = signal_parts[0]
                    format_str = signal_parts[1]
                    adc_units = signal_parts[2]
                    adc_resolution = int(signal_parts[3]) if signal_parts[3].isdigit() else None
                    adc_zero = int(signal_parts[4]) if signal_parts[4].lstrip('-').isdigit() else None
                    initial_value = int(signal_parts[5]) if signal_parts[5].lstrip('-').isdigit() else None
                    description = signal_parts[8] if len(signal_parts) > 8 else None
            
            metadata = HEAMetadata(
                record_name=record_name,
                num_signals=num_signals,
                sampling_frequency=sampling_frequency,
                num_samples=num_samples,
                duration_seconds=duration_seconds,
                time=time,
                date=date,
                signal_file=signal_file,
                format=format_str,
                adc_units=adc_units,
                adc_resolution=adc_resolution,
                adc_zero=adc_zero,
                initial_value=initial_value,
                description=description
            )
            
            logger.debug(f"Parsed .hea file: {hea_file_path}")
            return metadata
            
        except Exception as e:
            logger.error(f"Error parsing .hea file {hea_file_path}: {str(e)}")
            return None
    
    @staticmethod
    def get_hea_path(patient_id: str, training_dir: str = "./data/cinc2017/training") -> str:
        """
        Get the path to .hea file for a patient.
        
        Args:
            patient_id: Patient ID with path (e.g., 'A00/A00001')
            training_dir: Base training directory
            
        Returns:
            Path to .hea file
        """
        return os.path.join(training_dir, f"{patient_id}.hea")
    
    @staticmethod
    def load_patient_metadata(patient_id: str, training_dir: str = "./data/cinc2017/training") -> Optional[HEAMetadata]:
        """
        Load metadata from .hea file for a patient.
        
        Args:
            patient_id: Patient ID with path (e.g., 'A00/A00001')
            training_dir: Base training directory
            
        Returns:
            HEAMetadata object or None if not found
        """
        hea_path = HEAParser.get_hea_path(patient_id, training_dir)
        return HEAParser.parse_hea_file(hea_path)

