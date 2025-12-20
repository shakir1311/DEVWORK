"""
XAI Explainer for ECG-DualNet
Generates explanations for ECG classification predictions using GradCAM.
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from typing import Dict, List, Tuple, Optional


class GradCAM1D:
    """
    GradCAM for 1D convolutional networks (ECG signal branch).
    Generates importance scores for each timestep in the signal.
    """
    def __init__(self, model: nn.Module, target_layer: nn.Module):
        self.model = model
        self.target_layer = target_layer
        self.gradients = None
        self.activations = None
        
        # Register hooks
        self._register_hooks()
    
    def _register_hooks(self):
        def forward_hook(module, input, output):
            self.activations = output.detach()
        
        def backward_hook(module, grad_in, grad_out):
            self.gradients = grad_out[0].detach()
        
        self.target_layer.register_forward_hook(forward_hook)
        self.target_layer.register_full_backward_hook(backward_hook)
    
    def generate(self, input_tensor: torch.Tensor, class_idx: int = None) -> np.ndarray:
        """
        Generate GradCAM heatmap for 1D input.
        
        Args:
            input_tensor: Input ECG signal
            class_idx: Target class (None = predicted class)
            
        Returns:
            importance_scores: (length,) normalized importance for each timestep
        """
        self.model.eval()
        
        # Forward pass
        output = self.model(input_tensor)
        
        if class_idx is None:
            class_idx = output.argmax(dim=1).item()
        
        # Backward pass
        self.model.zero_grad()
        one_hot = torch.zeros_like(output)
        one_hot[0, class_idx] = 1
        output.backward(gradient=one_hot, retain_graph=True)
        
        # Compute weights (global average pooling of gradients)
        weights = self.gradients.mean(dim=-1, keepdim=True)  # (batch, channels, 1)
        
        # Compute weighted combination
        cam = (weights * self.activations).sum(dim=1)  # (batch, length)
        cam = F.relu(cam)  # ReLU to keep positive contributions
        
        # Normalize
        cam = cam.squeeze().cpu().numpy()
        if cam.max() > 0:
            cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)
        
        return cam


class GradCAM2D:
    """
    GradCAM for 2D convolutional networks (spectrogram branch).
    Generates a heatmap showing important time-frequency regions.
    """
    def __init__(self, model: nn.Module, target_layer: nn.Module):
        self.model = model
        self.target_layer = target_layer
        self.gradients = None
        self.activations = None
        
        self._register_hooks()
    
    def _register_hooks(self):
        def forward_hook(module, input, output):
            self.activations = output.detach()
        
        def backward_hook(module, grad_in, grad_out):
            self.gradients = grad_out[0].detach()
        
        self.target_layer.register_forward_hook(forward_hook)
        self.target_layer.register_full_backward_hook(backward_hook)
    
    def generate(self, ecg_lead: torch.Tensor, spectrogram: torch.Tensor, 
                 class_idx: int = None) -> np.ndarray:
        """
        Generate GradCAM heatmap for spectrogram.
        
        Returns:
            heatmap: (H, W) normalized importance heatmap
        """
        self.model.eval()
        
        output = self.model(ecg_lead, spectrogram)
        
        if class_idx is None:
            class_idx = output.argmax(dim=1).item()
        
        self.model.zero_grad()
        one_hot = torch.zeros_like(output)
        one_hot[0, class_idx] = 1
        output.backward(gradient=one_hot, retain_graph=True)
        
        # Weights from gradients
        weights = self.gradients.mean(dim=(2, 3), keepdim=True)
        
        # Weighted combination
        cam = (weights * self.activations).sum(dim=1)
        cam = F.relu(cam)
        
        # Normalize
        cam = cam.squeeze().cpu().numpy()
        if cam.max() > 0:
            cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)
        
        return cam


class ECGExplainer:
    """
    Main explainer class for ECG-DualNet.
    Provides unified interface for generating explanations.
    """
    
    # OUR class order for display: N=0, A=1, O=2, ~=3
    CLASS_NAMES = ['N', 'A', 'O', '~']
    CLASS_DESCRIPTIONS = {
        'N': 'Normal Sinus Rhythm',
        'A': 'Atrial Fibrillation',
        'O': 'Other Rhythm',
        '~': 'Noisy/Unclassifiable'
    }
    
    # ECG-DualNet class order: N=0, O=1, A=2, ~=3
    # Mapping: DUALNET[i] -> OUR[DUALNET_TO_OURS[i]]
    DUALNET_TO_OURS = {0: 0, 1: 2, 2: 1, 3: 3}  # N->N, O(1)->O(2), A(2)->A(1), ~->~
    OURS_TO_DUALNET = {0: 0, 1: 2, 2: 1, 3: 3}  # Inverse mapping
    
    def __init__(self, model, device='cpu'):
        """
        Initialize explainer with ECG-DualNet model.
        
        Args:
            model: ECGDualNetWrapper instance
            device: Device for computation
        """
        self.model = model
        self.device = device
        self.model.eval()
        
    def explain(self, ecg_signal: np.ndarray, predicted_class: int = None) -> Dict:
        """
        Generate explanation for ECG classification.
        
        Args:
            ecg_signal: Raw ECG signal (1D numpy array)
            predicted_class: Class to explain (None = predicted class)
            
        Returns:
            Dictionary containing:
            - signal_importance: Per-sample importance scores
            - peak_regions: List of (start, end) tuples for high-importance regions
            - explanation_text: Human-readable explanation
        """
        # FORCE CPU for XAI - MPS doesn't support unfold_backward
        cpu_device = torch.device('cpu')
        
        # Move model to CPU temporarily for gradient computation
        original_device = next(self.model.parameters()).device
        self.model.to(cpu_device)
        
        try:
            # Convert to tensor on CPU
            signal_tensor = torch.from_numpy(ecg_signal).float().unsqueeze(0).unsqueeze(0)
            signal_tensor = signal_tensor.to(cpu_device)
            
            # Get prediction - note: DualNet returns probs in its order (N,O,A,~)
            with torch.no_grad():
                preds, dualnet_probs = self.model.predict(signal_tensor)
                
                # Remap probabilities from DualNet order (N,O,A,~) to our order (N,A,O,~)
                dualnet_probs_np = dualnet_probs.cpu().numpy()[0]
                our_probs = np.array([
                    dualnet_probs_np[0],  # N -> N
                    dualnet_probs_np[2],  # A -> A (DualNet A is at index 2)
                    dualnet_probs_np[1],  # O -> O (DualNet O is at index 1)
                    dualnet_probs_np[3],  # ~ -> ~
                ])
                
                # Use passed class or determine from our-order probs
                if predicted_class is not None:
                    predicted_idx = predicted_class
                else:
                    predicted_idx = np.argmax(our_probs)
            
            # Generate importance scores using input gradients
            signal_tensor = signal_tensor.detach().clone().requires_grad_(True)
            
            # Forward pass with gradients
            self.model.train()  # Enable gradients
            ecg_lead, spectrogram = self.model.preprocess(signal_tensor)
            output = self.model.model(ecg_lead, spectrogram)
            
            # Backward for target class
            self.model.zero_grad()
            one_hot = torch.zeros_like(output)
            one_hot[0, predicted_idx] = 1
            output.backward(gradient=one_hot)
            
            # Get input gradients
            gradients = signal_tensor.grad.abs().squeeze().cpu().numpy()
            
            self.model.eval()
            
            # Normalize importance
            if gradients.max() > 0:
                importance = (gradients - gradients.min()) / (gradients.max() - gradients.min() + 1e-8)
            else:
                importance = gradients
            
            # Smooth importance scores (wider window for meaningful regions)
            importance = self._smooth_importance(importance, window_size=100)
            
            # Find peak regions using adaptive threshold
            peak_regions = self._find_peak_regions(importance, threshold=0.3, min_duration=50)
            
            # Generate text explanation (use our_probs which is in correct order)
            explanation_text = self._generate_explanation_text(
                predicted_idx, our_probs, peak_regions, len(ecg_signal)
            )
            
            return {
                'signal_importance': importance.tolist(),
                'peak_regions': [(int(start), int(end)) for start, end in peak_regions],  # Convert numpy int64 to Python int
                'explanation_text': explanation_text,
                'predicted_class': self.CLASS_NAMES[predicted_idx],
                'class_probabilities': {
                    self.CLASS_NAMES[i]: float(our_probs[i]) 
                    for i in range(len(self.CLASS_NAMES))
                }
            }
        finally:
            # Restore model to original device
            self.model.to(original_device)
            self.model.eval()
    
    def _smooth_importance(self, importance: np.ndarray, window_size: int = 100) -> np.ndarray:
        """Apply Gaussian-like smoothing to importance scores."""
        # Use a wider window for more meaningful regions
        kernel = np.ones(window_size) / window_size
        smoothed = np.convolve(importance, kernel, mode='same')
        # Normalize to 0-1 range
        if smoothed.max() > 0:
            smoothed = (smoothed - smoothed.min()) / (smoothed.max() - smoothed.min() + 1e-8)
        return smoothed
    
    def _find_peak_regions(self, importance: np.ndarray, threshold: float = 0.3, 
                           min_duration: int = 300) -> List[Tuple[int, int]]:  # 1 second min @ 300Hz
        """
        Find clinically meaningful regions with high importance.
        Always uses fixed 3-second windows (900 samples @ 300Hz) centered on peak importance.
        This ensures enough ECG context for clinical interpretation (3-5 heartbeats).
        """
        # Always use top-K peak approach for clinically meaningful 3-second windows
        # This ensures doctors see enough context (multiple RR intervals)
        return self._find_top_peaks(importance, n_peaks=3, window=900)  # 3 second windows
    
    def _find_top_peaks(self, importance: np.ndarray, n_peaks: int = 3, 
                        window: int = 900) -> List[Tuple[int, int]]:  # 3 seconds @ 300Hz
        """Find top N peak regions by importance value."""
        regions = []
        temp_importance = importance.copy()
        
        for _ in range(n_peaks):
            peak_idx = np.argmax(temp_importance)
            if temp_importance[peak_idx] < 0.1:  # Don't include very low peaks
                break
            
            # Define region around peak
            start = max(0, peak_idx - window // 2)
            end = min(len(importance), peak_idx + window // 2)
            regions.append((start, end))
            
            # Zero out this region to find next peak
            temp_importance[start:end] = 0
        
        return sorted(regions, key=lambda x: x[0])  # Sort by start time
    
    def _generate_explanation_text(self, class_idx: int, probs: np.ndarray, 
                                    peak_regions: List[Tuple], signal_length: int) -> str:
        """Generate human-readable explanation text."""
        class_name = self.CLASS_NAMES[class_idx]
        confidence = probs[class_idx] * 100
        
        # Confidence level description
        if confidence >= 70:
            conf_desc = "high"
        elif confidence >= 40:
            conf_desc = "moderate"
        else:
            conf_desc = "low"
        
        # Base explanation
        explanation = f"**{self.CLASS_DESCRIPTIONS[class_name]}** detected ({conf_desc} confidence: {confidence:.0f}%). "
        
        # Add region info
        if peak_regions:
            total_duration = sum((end - start) / 300 for start, end in peak_regions)
            explanation += f"Analysis focused on {len(peak_regions)} region(s) spanning {total_duration:.1f}s total. "
        
        # Add class-specific clinical insights
        if class_name == 'A':
            explanation += "AF characteristics: irregularly irregular rhythm, absent P-waves, fibrillatory baseline."
        elif class_name == 'N':
            explanation += "NSR characteristics: regular R-R intervals, consistent P-wave morphology, normal PR interval."
        elif class_name == 'O':
            explanation += "Possible: ectopic beats, conduction abnormalities, or rhythm variations requiring clinical review."
        elif class_name == '~':
            explanation += "Signal quality insufficient for reliable classification - consider re-acquisition."
        
        return explanation


def create_explainer(model, device='cpu') -> ECGExplainer:
    """Factory function to create an ECGExplainer instance."""
    return ECGExplainer(model, device)

