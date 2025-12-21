"""
ML Inference Processor Module
Runs trained ML models on incoming ECG data for classification.
"""

import logging
import os
import numpy as np
from typing import Dict, Any, Optional
import re

logger = logging.getLogger(__name__)

# Import base class
from ecg_processor import ECGProcessor
from processors.resnet1d_official import ResNet1D as HSD1503ResNet1D  # Official implementation
from processors.xai_explainer import ECGExplainer

# Try to import PyTorch and sklearn
try:
    import torch
    PYTORCH_AVAILABLE = True
except ImportError:
    PYTORCH_AVAILABLE = False
    logger.warning("PyTorch not available - deep learning models disabled")

try:
    import joblib
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("joblib not available - sklearn models disabled")


# Constants
FIXED_LENGTH_SAMPLES = 9000  # 30 seconds at 300Hz
CLASS_NAMES = ['N', 'A', 'O', '~']
CLASS_DESCRIPTIONS = {
    'N': 'Normal sinus rhythm',
    'A': 'Atrial Fibrillation',
    'O': 'Other rhythm',
    '~': 'Noisy (too noisy to classify)'
}

# Feature Extraction Helpers for Traditional ML
def extract_features_from_signal(signal, sampling_rate=300):
    """
    Extract handcrafted features from ECG signal.
    Must match ML/utils/feature_extraction.py exactly to ensure valid inference.
    """
    signal = np.array(signal).flatten()
    features = {}
    
    # --- Statistical ---
    try:
        from scipy import stats
        features['mean'] = np.mean(signal)
        features['std'] = np.std(signal)
        features['min'] = np.min(signal)
        features['max'] = np.max(signal)
        features['range'] = np.max(signal) - np.min(signal)
        features['median'] = np.median(signal)
        features['skewness'] = stats.skew(signal)
        features['kurtosis'] = stats.kurtosis(signal)
        features['rms'] = np.sqrt(np.mean(signal**2))
        features['iqr'] = np.percentile(signal, 75) - np.percentile(signal, 25)
    except ImportError:
        # Fallback if scipy not present (though models might perform poorly)
        logger.warning("Scipy not found for statistical features")
        features.update({k: 0 for k in ['skewness', 'kurtosis']}) 
        # others can be computed with numpy

    # --- RR / Peak Detection ---
    try:
        from scipy.signal import find_peaks
        min_distance = int(0.5 * sampling_rate)
        peaks, _ = find_peaks(signal, distance=min_distance, height=np.mean(signal))
        
        if len(peaks) < 2:
            rr_vals = {'mean_rr': 0, 'sdnn': 0, 'rmssd': 0, 'pnn50': 0, 
                       'heart_rate': 0, 'hr_std': 0, 'num_peaks': len(peaks)}
        else:
            rr_intervals = np.diff(peaks) / sampling_rate * 1000
            rr_diff = np.diff(rr_intervals)
            mean_rr = np.mean(rr_intervals)
            sdnn = np.std(rr_intervals)
            rmssd = np.sqrt(np.mean(rr_diff**2)) if len(rr_diff) > 0 else 0
            pnn50 = np.sum(np.abs(rr_diff) > 50) / len(rr_diff) * 100 if len(rr_diff) > 0 else 0
            rr_vals = {
                'mean_rr': mean_rr, 'sdnn': sdnn, 'rmssd': rmssd, 'pnn50': pnn50,
                'heart_rate': 60000 / mean_rr if mean_rr > 0 else 0,
                'hr_std': 60000 / sdnn if sdnn > 0 else 0,
                'num_peaks': len(peaks)
            }
        features.update(rr_vals)
    except ImportError:
        logger.warning("Scipy not found for peak detection")
        features.update({k: 0 for k in ['mean_rr', 'sdnn', 'rmssd', 'pnn50', 'heart_rate', 'hr_std', 'num_peaks']})

    # --- Frequency Domain ---
    fft_vals = np.abs(np.fft.rfft(signal))
    fft_freqs = np.fft.rfftfreq(len(signal), 1/sampling_rate)
    
    lf_mask = (fft_freqs >= 0.04) & (fft_freqs < 0.15)
    hf_mask = (fft_freqs >= 0.15) & (fft_freqs < 0.4)
    
    lf_power = np.sum(fft_vals[lf_mask]**2) if np.any(lf_mask) else 0
    hf_power = np.sum(fft_vals[hf_mask]**2) if np.any(hf_mask) else 0
    total_power = np.sum(fft_vals**2)
    
    freq_vals = {
        'lf_power': lf_power,
        'hf_power': hf_power,
        'total_power': total_power,
        'lf_hf_ratio': lf_power / hf_power if hf_power > 0 else 0,
        'dominant_freq': fft_freqs[np.argmax(fft_vals)] if len(fft_vals) > 0 else 0
    }
    features.update(freq_vals)
    
    # Sort keys to ensure consistenc order with training
    keys = sorted(features.keys())
    return np.array([features[k] for k in keys]).reshape(1, -1)


class MLInferenceProcessor(ECGProcessor):
    """Processor for running ML inference on ECG signals."""
    
    def __init__(self, models_dir: str = None):
        """
        Initialize ML inference processor.
        
        Args:
            models_dir: Directory containing trained model files
        """
        super().__init__("ml_inference")
        
        # Default models directory
        if models_dir is None:
            models_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')
        
        self.models_dir = models_dir
        self.current_model = None
        self.current_model_path = None
        self.current_model_type = None
        self.device = None
        self.skip_xai = False  # Set to True for bulk experiments
        
        # Setup device
        if PYTORCH_AVAILABLE:
            if torch.cuda.is_available():
                self.device = torch.device('cuda')
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                self.device = torch.device('mps')
            else:
                self.device = torch.device('cpu')
            logger.info(f"[MLInference] Using device: {self.device}")
        
        # Scan for available models
        self._available_models = self._scan_models()
        logger.info(f"[MLInference] Found {len(self._available_models)} model(s) in {models_dir}")
    
    def _scan_models(self) -> list:
        """Scan models directory for available model files."""
        models = []
        if not os.path.exists(self.models_dir):
            logger.warning(f"[MLInference] Models directory not found: {self.models_dir}")
            return models
        
        for filename in os.listdir(self.models_dir):
            if filename.endswith('.pth') or filename.endswith('.pt'):
                models.append({
                    'filename': filename,
                    'path': os.path.join(self.models_dir, filename),
                    'type': self._detect_model_type(filename),
                    'framework': 'pytorch'
                })
            elif filename.endswith('.pkl') or filename.endswith('.joblib'):
                models.append({
                    'filename': filename,
                    'path': os.path.join(self.models_dir, filename),
                    'type': self._detect_model_type(filename),
                    'framework': 'sklearn'
                })
        
        # Manually add ECG-DualNet (internal pretrained model)
        models.append({
            'filename': '🏆 ECG-DualNet (Pretrained)',
            'path': 'ecg_dualnet',
            'type': 'ecg_dualnet',
            'framework': 'pytorch'
        })
        
        return sorted(models, key=lambda x: x['filename'])
    
    def _detect_model_type(self, filename: str) -> str:
        """
        Detect model type from filename.
        
        These must match the shortnames in ML/gui/main_window.py MODEL_SHORTNAMES:
        - tcnn -> tinycnn
        - mlp -> mlp  
        - dtree -> decision_tree
        - cnn -> cnn (SimpleCNN)
        - rf -> random_forest
        - lstm -> lstm
        - crnn -> crnn
        - resnet -> resnet1d
        - bilstm_attn -> attention
        - encase -> net1d
        """
        filename_lower = filename.lower()
        
        # Exact ML shortname matches (from MODEL_SHORTNAMES)
        if 'tcnn' in filename_lower or 'tiny' in filename_lower:
            return 'tinycnn'
        elif 'mlp' in filename_lower:
            return 'mlp'
        elif 'dtree' in filename_lower or ('tree' in filename_lower and 'decision' in filename_lower):
            return 'decision_tree'
        elif 'rf' in filename_lower or 'forest' in filename_lower:
            return 'random_forest'
        elif 'bilstm_attn' in filename_lower or 'attention' in filename_lower:
            return 'attention'
        elif 'crnn' in filename_lower:
            return 'crnn'
        elif 'resnet' in filename_lower:
            # Check for HSD variant
            if 'hsd' in filename_lower:
                return 'resnet1d_hsd1503'
            return 'resnet1d'
        elif 'encase' in filename_lower or 'net1d' in filename_lower:
            return 'net1d'
        elif 'lstm' in filename_lower:
            return 'lstm'
        elif 'simple' in filename_lower or 'simplecnn' in filename_lower:
            return 'simplecnn'
        elif 'dualnet' in filename_lower or 'ecg_dualnet' in filename_lower:
            return 'ecg_dualnet'
        elif 'cnn' in filename_lower:
            # Generic CNN (must be checked AFTER tcnn, crnn, simplecnn)
            return 'cnn'
        else:
            return 'unknown'
    
    def get_available_models(self) -> list:
        """Get list of available models."""
        return self._available_models
    
    def refresh_models(self):
        """Rescan models directory."""
        self._available_models = self._scan_models()
        return self._available_models
    
    def _load_ecg_dualnet(self) -> bool:
        """Load pretrained ECG-DualNet model from local ecg_dualnet folder."""
        try:
            import sys
            
            # Use local ecg_dualnet folder (setup_ecg_dualnet.py copies files here)
            edge_dir = os.path.dirname(os.path.dirname(__file__))
            ecg_dualnet_dir = os.path.join(edge_dir, 'ecg_dualnet')
            
            if not os.path.exists(ecg_dualnet_dir):
                logger.error(f"[MLInference] ECG-DualNet not found at {ecg_dualnet_dir}")
                logger.error("[MLInference] Run setup_ecg_dualnet.py to copy required files")
                return False
            
            # Add to path
            sys.path.insert(0, ecg_dualnet_dir)
            
            from ecg_dualnet_wrapper import get_pretrained_ecg_dualnet
            
            # Find pretrained weights
            pretrained_path = os.path.join(ecg_dualnet_dir, 'pretrained', 'ECGCNN_S_best_model.pt')
            
            if not os.path.exists(pretrained_path):
                logger.error(f"[MLInference] Pretrained weights not found at {pretrained_path}")
                return False
            
            device_str = 'cpu' if self.device == torch.device('cpu') else str(self.device)
            self.current_model = get_pretrained_ecg_dualnet(
                model_size='S', 
                device=device_str,
                model_path=pretrained_path
            )
            self.current_model_path = 'ECG-DualNet-S (Pretrained)'
            self.current_model_type = 'ecg_dualnet'
            logger.info("[MLInference] Loaded ECG-DualNet-S pretrained model (86.34% accuracy)")
            return True
        except Exception as e:
            logger.error(f"[MLInference] Failed to load ECG-DualNet: {e}", exc_info=True)
            return False
    
    def load_model(self, model_path: str) -> bool:
        """
        Load a model from file.
        
        Args:
            model_path: Path to model file, or 'ecg_dualnet' for pretrained ECG-DualNet
            
        Returns:
            True if loaded successfully
        """
        # Special case: ECG-DualNet loads pretrained from ML directory
        if model_path == 'ecg_dualnet' or 'dualnet' in model_path.lower():
            return self._load_ecg_dualnet()
        
        if not os.path.exists(model_path):
            logger.error(f"[MLInference] Model file not found: {model_path}")
            return False
        
        filename = os.path.basename(model_path)
        model_type = self._detect_model_type(filename)
        
        try:
            # Check for Traditional ML first (even if .pth)
            if model_type in ['random_forest', 'decision_tree']:
                 if not SKLEARN_AVAILABLE:
                    logger.error("[MLInference] joblib/sklearn not available for traditional ML models")
                    return False
                 
                 # Traditional ML models in this project are joblib dumped dicts
                 # even if they have .pth extension
                 data = joblib.load(model_path)
                 
                 # wrapper to expose predict/predict_proba like the original class
                 class ScikitWrapper:
                     def __init__(self, model_data):
                         self.model = model_data['model']
                         self.scaler = model_data['scaler']
                     
                     def predict(self, X):
                         # X is raw signal (1, 9000) passed from process()
                         # We need to extract features first
                         features = extract_features_from_signal(X)
                         return self.model.predict(self.scaler.transform(features))

                     def predict_proba(self, X):
                         features = extract_features_from_signal(X)
                         return self.model.predict_proba(self.scaler.transform(features))
                 
                 self.current_model = ScikitWrapper(data)
                 
            elif model_path.endswith('.pth') or model_path.endswith('.pt'):
                # Load PyTorch model
                if not PYTORCH_AVAILABLE:
                    logger.error("[MLInference] PyTorch not available")
                    return False
                
                # Import model definitions
                from processors.model_definitions import MODEL_CLASSES
                
                if model_type not in MODEL_CLASSES:
                    logger.error(f"[MLInference] Unknown model type: {model_type}")
                    return False
                
                # Instantiate model
                if model_type == 'resnet1d_hsd1503':
                    # Correct parameters matching the checkpoint
                    self.current_model = HSD1503ResNet1D(
                        in_channels=1,
                        base_filters=64,
                        kernel_size=16,
                        stride=2,
                        groups=32,
                        n_block=20,  # Checkpoint has 20 blocks, not 48!
                        n_classes=4,
                        downsample_gap=6,
                        increasefilter_gap=4,  # Doubles every 4 blocks to reach 1024
                        use_do=True
                    )
                elif model_type == 'ecg_dualnet':
                    # ECG-DualNet: Load from ML directory wrapper
                    import sys
                    ml_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'ML'))
                    sys.path.insert(0, ml_dir)
                    sys.path.insert(0, os.path.join(ml_dir, 'ecg_dualnet_repo'))
                    from models.ecg_dualnet_wrapper import get_pretrained_ecg_dualnet
                    
                    device_str = 'cpu' if self.device == torch.device('cpu') else str(self.device)
                    self.current_model = get_pretrained_ecg_dualnet(model_size='S', device=device_str)
                    self.current_model_path = model_path
                    self.current_model_type = model_type
                    logger.info(f"[MLInference] Loaded ECG-DualNet-S pretrained model")
                    return True
                else:
                    model_class = MODEL_CLASSES[model_type]
                    self.current_model = model_class()
                
                # Load state dict
                state_dict = torch.load(model_path, map_location=self.device)
                
                # Handle if loaded object is a full model
                if isinstance(state_dict, torch.nn.Module):
                    state_dict = state_dict.state_dict()
                
                
                # Handle torch.compile prefixed keys
                if any(k.startswith('_orig_mod.') for k in state_dict.keys()):
                    state_dict = {k.replace('_orig_mod.', ''): v for k, v in state_dict.items()}
                
                # Load directly - official ResNet1D should match checkpoint structure
                self.current_model.load_state_dict(state_dict, strict=False)
                self.current_model.to(self.device)
                self.current_model.eval()
                
            elif model_path.endswith('.pkl') or model_path.endswith('.joblib'):
                # Load sklearn model (if named correctly)
                if not SKLEARN_AVAILABLE:
                    logger.error("[MLInference] joblib not available for sklearn models")
                    return False
                
                self.current_model = joblib.load(model_path)
            
            self.current_model_path = model_path
            self.current_model_type = model_type
            logger.info(f"[MLInference] Loaded model: {filename} ({model_type})")
            return True
            
        except Exception as e:
            logger.error(f"[MLInference] Failed to load model: {e}", exc_info=True)
            self.current_model = None
            return False
    
    def preprocess(self, ecg_data: np.ndarray) -> np.ndarray:
        """
        Preprocess ECG data for inference.
        
        Args:
            ecg_data: Raw ECG signal
            
        Returns:
            Preprocessed ECG signal (normalized, fixed length)
        """
        # Ensure 1D
        if ecg_data.ndim > 1:
            ecg_data = ecg_data.flatten()
        
        # Normalize (z-score)
        mean = np.mean(ecg_data)
        std = np.std(ecg_data)
        if std > 0:
            ecg_data = (ecg_data - mean) / std
        else:
            ecg_data = ecg_data - mean
        
        # Fix length to 9000 samples (30 seconds at 300Hz)
        if len(ecg_data) >= FIXED_LENGTH_SAMPLES:
            ecg_data = ecg_data[:FIXED_LENGTH_SAMPLES]
        else:
            # Pad with zeros
            padding = FIXED_LENGTH_SAMPLES - len(ecg_data)
            ecg_data = np.pad(ecg_data, (0, padding), 'constant')
        
        return ecg_data.astype(np.float32)
    
    def process(self, ecg_data: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run inference on ECG data.
        
        Args:
            ecg_data: ECG signal array
            metadata: Metadata dictionary
            
        Returns:
            Dictionary with classification results
        """
        results = {
            'model_loaded': self.current_model is not None,
            'model_path': self.current_model_path,
            'model_type': self.current_model_type,
            'classification': None,
            'classification_description': None,
            'confidence': None,
            'probabilities': None,
        }
        
        if self.current_model is None:
            logger.warning("[MLInference] No model loaded - skipping inference")
            return results
        
        try:
            # Preprocess
            processed = self.preprocess(ecg_data)
            
            # Determine model type (PyTorch vs Sklearn)
            is_pytorch = False
            if PYTORCH_AVAILABLE and isinstance(self.current_model, torch.nn.Module):
                is_pytorch = True
                
            if not is_pytorch and hasattr(self.current_model, 'predict'):
                # sklearn model
                features = processed.reshape(1, -1)
                prediction = self.current_model.predict(features)[0]
                
                if hasattr(self.current_model, 'predict_proba'):
                    probabilities = self.current_model.predict_proba(features)[0]
                    confidence = float(np.max(probabilities))
                    results['probabilities'] = [float(p) for p in probabilities]
                else:
                    confidence = None
                
                predicted_class = CLASS_NAMES[int(prediction)]
                
            else:
                # PyTorch model
                with torch.no_grad():
                    # Add batch and channel dimensions: (9000,) -> (1, 1, 9000)
                    x = torch.from_numpy(processed).unsqueeze(0).unsqueeze(0).to(self.device)
                    
                    # ECG-DualNet has its own predict() method
                    if self.current_model_type == 'ecg_dualnet':
                        # ECG-DualNet returns (predictions, probabilities)
                        preds, probs = self.current_model.predict(x)
                        # ECG-DualNet class order: N=0, O=1, A=2, ~=3
                        # Our class order:        N=0, A=1, O=2, ~=3
                        DUALNET_TO_OURS = {0: 0, 1: 2, 2: 1, 3: 3}  # N->N, O->O, A->A, ~->~
                        
                        dualnet_idx = preds.item()
                        predicted_idx = DUALNET_TO_OURS[dualnet_idx]
                        
                        # Remap probabilities too: [N, O, A, ~] -> [N, A, O, ~]
                        dualnet_probs = probs.cpu().numpy()[0]
                        probabilities = np.array([
                            dualnet_probs[0],  # N -> N
                            dualnet_probs[2],  # A -> A
                            dualnet_probs[1],  # O -> O
                            dualnet_probs[3],  # ~ -> ~
                        ])
                    else:
                        # Standard PyTorch model
                        outputs = self.current_model(x)
                        probabilities = torch.softmax(outputs, dim=1).cpu().numpy()[0]
                        predicted_idx = int(np.argmax(probabilities))
                    
                    predicted_class = CLASS_NAMES[predicted_idx]
                    confidence = float(probabilities[predicted_idx])
            results['probabilities'] = [float(p) for p in probabilities]
            
            # Add named class probabilities for consumers (WEB, GUI)
            results['class_probabilities'] = {
                name: float(prob) for name, prob in zip(CLASS_NAMES, results['probabilities'])
            }
            
            # Generate XAI explanation for ECG-DualNet (unless skipped)
            logger.info(f"[MLInference] Model type: {self.current_model_type}")
            if self.current_model_type == 'ecg_dualnet' and not self.skip_xai:
                logger.info("[MLInference] Generating XAI explanation...")
                try:
                    explainer = ECGExplainer(self.current_model, self.device)
                    explanation = explainer.explain(processed, predicted_idx)
                    results['explanation'] = {
                        'signal_importance': explanation['signal_importance'],
                        'peak_regions': explanation['peak_regions'],
                        'explanation_text': explanation['explanation_text']
                    }
                    logger.info(f"[MLInference] ✓ Generated XAI explanation with {len(explanation['peak_regions'])} peak regions")
                except Exception as e:
                    logger.warning(f"[MLInference] XAI explanation failed: {e}", exc_info=True)
                    results['explanation'] = None
            elif self.current_model_type == 'ecg_dualnet' and self.skip_xai:
                logger.debug("[MLInference] Skipping XAI (disabled for performance)")
            else:
                logger.info(f"[MLInference] Skipping XAI (model type is not ecg_dualnet)")
            
            results['classification'] = predicted_class
            results['classification_description'] = CLASS_DESCRIPTIONS.get(predicted_class, 'Unknown')
            results['confidence'] = confidence
            
            logger.info(f"[MLInference] Classification: {predicted_class} ({results['classification_description']}) - Confidence: {confidence*100:.1f}%" if confidence else f"[MLInference] Classification: {predicted_class}")
            
        except Exception as e:
            logger.error(f"[MLInference] Inference failed: {e}", exc_info=True)
            results['error'] = str(e)
        
        return results
