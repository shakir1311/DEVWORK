"""
ECG Processing Modules
Modular processors for ECG signal analysis.
"""

from .heart_rate_processor import HeartRateProcessor
from .ml_inference_processor import MLInferenceProcessor

__all__ = ['HeartRateProcessor', 'MLInferenceProcessor']
