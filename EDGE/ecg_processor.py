"""
ECG Processor Base Module
Base class for modular ECG processing.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any
import numpy as np

logger = logging.getLogger(__name__)


class ECGProcessor(ABC):
    """Base class for ECG processing modules."""
    
    def __init__(self, name: str):
        """
        Initialize processor.
        
        Args:
            name: Name of the processor module
        """
        self.name = name
        self.enabled = True
    
    @abstractmethod
    def process(self, ecg_data: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process ECG data.
        
        Args:
            ecg_data: ECG signal array (samples)
            metadata: Metadata dictionary with sampling_rate, duration_seconds, etc.
            
        Returns:
            Dictionary with processing results
        """
        pass
    
    def enable(self):
        """Enable this processor."""
        self.enabled = True
        logger.info(f"Processor '{self.name}' enabled")
    
    def disable(self):
        """Disable this processor."""
        self.enabled = False
        logger.info(f"Processor '{self.name}' disabled")


class ProcessorPipeline:
    """Pipeline for running multiple ECG processors."""
    
    def __init__(self):
        """Initialize processor pipeline."""
        self.processors: list[ECGProcessor] = []
    
    def add_processor(self, processor: ECGProcessor):
        """
        Add a processor to the pipeline.
        
        Args:
            processor: ECGProcessor instance to add
        """
        self.processors.append(processor)
        logger.info(f"Added processor '{processor.name}' to pipeline")
    
    def remove_processor(self, name: str):
        """
        Remove a processor from the pipeline.
        
        Args:
            name: Name of processor to remove
        """
        self.processors = [p for p in self.processors if p.name != name]
        logger.info(f"Removed processor '{name}' from pipeline")
    
    def process(self, ecg_data: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run all enabled processors on ECG data.
        
        Args:
            ecg_data: ECG signal array
            metadata: Metadata dictionary
            
        Returns:
            Combined results from all processors
        """
        results = {
            'processors_run': [],
            'results': {}
        }
        
        for processor in self.processors:
            if processor.enabled:
                try:
                    logger.info(f"Running processor: {processor.name}")
                    processor_results = processor.process(ecg_data, metadata)
                    results['processors_run'].append(processor.name)
                    results['results'][processor.name] = processor_results
                    logger.info(f"✓ Processor '{processor.name}' completed")
                except Exception as e:
                    logger.error(f"Error in processor '{processor.name}': {e}", exc_info=True)
                    results['results'][processor.name] = {'error': str(e)}
        
        return results

