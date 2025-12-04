"""
Data Storage Module
Handles saving received ECG data to disk.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import numpy as np

from config import SAVE_RECEIVED_DATA, DATA_STORAGE_DIR

logger = logging.getLogger(__name__)


class DataStorage:
    """Handles storage of received ECG data."""
    
    def __init__(self, storage_dir: str = DATA_STORAGE_DIR):
        """
        Initialize data storage.
        
        Args:
            storage_dir: Directory to save ECG data
        """
        self.storage_dir = storage_dir
        self.enabled = SAVE_RECEIVED_DATA
        
        if self.enabled:
            os.makedirs(self.storage_dir, exist_ok=True)
            logger.info(f"Data storage enabled: {self.storage_dir}")
        else:
            logger.info("Data storage disabled")
    
    def save_ecg_data(self, ecg_data: np.ndarray, metadata: Dict[str, Any], 
                     processing_results: Dict[str, Any] = None) -> Optional[str]:
        """
        Save ECG data to disk.
        
        Args:
            ecg_data: ECG signal array
            metadata: Metadata dictionary
            processing_results: Optional processing results
            
        Returns:
            Path to saved file if successful, None otherwise
        """
        if not self.enabled:
            return None
        
        try:
            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"ecg_{timestamp}.npz"
            filepath = os.path.join(self.storage_dir, filename)
            
            # Prepare data to save
            save_dict = {
                'ecg_data': ecg_data,
                'metadata': metadata,
            }
            
            if processing_results:
                save_dict['processing_results'] = processing_results
            
            # Save as compressed numpy array
            np.savez_compressed(filepath, **save_dict)
            
            # Also save metadata as JSON for easy reading
            json_filename = f"ecg_{timestamp}_metadata.json"
            json_filepath = os.path.join(self.storage_dir, json_filename)
            
            json_metadata = {
                'timestamp': timestamp,
                'metadata': {k: float(v) if isinstance(v, (np.integer, np.floating)) else v 
                            for k, v in metadata.items()},
            }
            
            if processing_results:
                # Convert numpy types to native Python types for JSON
                json_processing = {}
                for key, value in processing_results.items():
                    if isinstance(value, dict):
                        json_processing[key] = {
                            k: float(v) if isinstance(v, (np.integer, np.floating)) else v
                            for k, v in value.items()
                        }
                    else:
                        json_processing[key] = value
                json_metadata['processing_results'] = json_processing
            
            with open(json_filepath, 'w') as f:
                json.dump(json_metadata, f, indent=2)
            
            logger.info(f"✓ Saved ECG data to {filepath}")
            logger.info(f"✓ Saved metadata to {json_filepath}")
            
            return filepath
            
        except Exception as e:
            logger.error(f"Error saving ECG data: {e}", exc_info=True)
            return None

