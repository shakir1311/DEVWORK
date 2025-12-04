"""
Heart Rate Processor Module
Calculates heart rate from ECG signal.
"""

import logging
import numpy as np
from typing import Dict, Any

from ecg_processor import ECGProcessor

logger = logging.getLogger(__name__)


class HeartRateProcessor(ECGProcessor):
    """Processor for calculating heart rate from ECG signal."""
    
    def __init__(self):
        """Initialize heart rate processor."""
        super().__init__("heart_rate")
    
    def process(self, ecg_data: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate heart rate from ECG signal.
        
        Uses improved R-peak detection based on local maxima and adaptive thresholding.
        
        Args:
            ecg_data: ECG signal array
            metadata: Metadata with sampling_rate, etc.
            
        Returns:
            Dictionary with heart rate results
        """
        sampling_rate = metadata.get('sampling_rate', 300)
        duration_seconds = metadata.get('duration_seconds', len(ecg_data) / sampling_rate)
        
        try:
            # Normalize ECG data (subtract mean to center around zero)
            ecg_normalized = ecg_data - np.mean(ecg_data)
            
            # Apply moving average filter to smooth the signal (reduce noise)
            window_size = int(sampling_rate * 0.02)  # 20ms window
            if window_size < 3:
                window_size = 3
            if window_size % 2 == 0:
                window_size += 1  # Make odd for symmetric window
            
            # Simple moving average
            ecg_smooth = np.convolve(ecg_normalized, np.ones(window_size)/window_size, mode='same')
            
            # Calculate adaptive threshold based on signal statistics
            # Use percentile-based threshold to be more robust to outliers
            signal_peak = np.percentile(ecg_smooth, 95)  # 95th percentile as peak estimate
            signal_median = np.median(np.abs(ecg_smooth))
            
            # Adaptive threshold: use higher of percentile-based or median-based
            threshold = max(signal_peak * 0.3, signal_median * 2.0)
            
            # Minimum distance between peaks (corresponds to max heart rate)
            # Allow up to 200 BPM = 0.3 seconds minimum
            min_peak_distance = int(sampling_rate * 0.3)  # 0.3 seconds minimum (200 BPM max)
            
            # Find local maxima above threshold
            peaks = []
            search_window = int(sampling_rate * 0.05)  # 50ms search window for local max
            
            i = search_window
            while i < len(ecg_smooth) - search_window:
                # Check if current point is above threshold
                if ecg_smooth[i] > threshold:
                    # Find local maximum in the search window
                    local_max_idx = i
                    local_max_val = ecg_smooth[i]
                    
                    # Search in window around current point
                    for j in range(max(0, i - search_window), min(len(ecg_smooth), i + search_window + 1)):
                        if ecg_smooth[j] > local_max_val:
                            local_max_val = ecg_smooth[j]
                            local_max_idx = j
                    
                    # Only add if this is the actual local maximum (not just a point in the window)
                    if local_max_idx == i or abs(local_max_idx - i) < search_window // 2:
                        # Check minimum distance from previous peak
                        if not peaks or (local_max_idx - peaks[-1]) >= min_peak_distance:
                            peaks.append(local_max_idx)
                            # Skip ahead to avoid finding multiple peaks in same region
                            i = local_max_idx + min_peak_distance
                            continue
                
                i += 1
            
            # Post-process: remove peaks that are too close together
            # This can happen if threshold was too low
            filtered_peaks = []
            for peak in peaks:
                if not filtered_peaks:
                    filtered_peaks.append(peak)
                else:
                    # Only add if far enough from previous peak
                    if (peak - filtered_peaks[-1]) >= min_peak_distance:
                        filtered_peaks.append(peak)
            
            # Calculate heart rate from RR intervals
            if len(filtered_peaks) >= 2:
                # Calculate RR intervals (time between consecutive R-peaks)
                rr_intervals = np.diff(filtered_peaks) / sampling_rate  # in seconds
                
                # Filter out physiologically implausible intervals
                # Normal range: 0.3s (200 BPM) to 2.0s (30 BPM)
                valid_rr = [rr for rr in rr_intervals if 0.3 <= rr <= 2.0]
                
                if len(valid_rr) >= 2:
                    valid_rr = np.array(valid_rr)
                    avg_rr_interval = np.mean(valid_rr)
                    heart_rate_bpm = 60.0 / avg_rr_interval
                    
                    # Calculate variability metrics
                    rr_std = np.std(valid_rr)
                    rr_cv = (rr_std / avg_rr_interval) * 100  # Coefficient of variation %
                    
                    # Use valid intervals for results
                    rr_intervals_result = valid_rr.tolist()
                else:
                    # Not enough valid intervals
                    heart_rate_bpm = None
                    rr_intervals_result = []
                    rr_std = None
                    rr_cv = None
            else:
                # Not enough peaks detected
                heart_rate_bpm = None
                rr_intervals_result = []
                rr_std = None
                rr_cv = None
            
            results = {
                'heart_rate_bpm': float(heart_rate_bpm) if heart_rate_bpm is not None else None,
                'num_peaks_detected': len(filtered_peaks),
                'rr_intervals_seconds': [float(x) for x in rr_intervals_result] if len(rr_intervals_result) > 0 else [],
                'rr_std_seconds': float(rr_std) if rr_std is not None else None,
                'rr_cv_percent': float(rr_cv) if rr_cv is not None else None,
                'peak_indices': filtered_peaks,
            }
            
            if heart_rate_bpm is not None:
                logger.info(f"Heart rate calculated: {heart_rate_bpm:.1f} BPM ({len(filtered_peaks)} peaks, {len(rr_intervals_result)} valid RR intervals)")
            else:
                logger.warning(f"Heart rate: Unable to calculate (detected {len(filtered_peaks)} peaks, need at least 2)")
            
            return results
            
        except Exception as e:
            logger.error(f"Error calculating heart rate: {e}", exc_info=True)
            return {'error': str(e)}

