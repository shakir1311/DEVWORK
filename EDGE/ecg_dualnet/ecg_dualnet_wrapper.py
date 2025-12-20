"""
ECG-DualNet Wrapper for CINC 2017 Classification
Uses the original preprocessing pipeline from ECG-DualNet
"""
import sys
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from pathlib import Path
from torchaudio.transforms import Spectrogram

# Add ECG-DualNet repository to path
DUALNET_PATH = Path(__file__).parent.parent / 'ecg_dualnet_repo'
sys.path.insert(0, str(DUALNET_PATH))

from ecg_classification import (ECGCNN, ECGAttNet, 
                                 ECGCNN_CONFIG_S, ECGCNN_CONFIG_M, ECGCNN_CONFIG_L, ECGCNN_CONFIG_XL,
                                 ECGAttNet_CONFIG_XL)


class ECGDualNetWrapper(nn.Module):
    """
    Wrapper for ECG-DualNet model using original preprocessing.
    """
    def __init__(self, model_path=None, model_size='S', device='cpu'):
        super(ECGDualNetWrapper, self).__init__()
        
        # Map model sizes to their configs
        config_map = {
            'S': ECGCNN_CONFIG_S,
            'M': ECGCNN_CONFIG_M,
            'L': ECGCNN_CONFIG_L,
            'XL': ECGCNN_CONFIG_XL,
        }
        
        self.model_size = model_size
        
        # Select architecture based on model size
        if model_size in ['S', 'M', 'L']:
            self.config = config_map[model_size].copy()
            self.model = ECGCNN(config=self.config)
        else:  # XL uses ECGAttNet
            self.config = ECGAttNet_CONFIG_XL.copy()
            self.model = ECGAttNet(config=self.config)
        
        # Preprocessing parameters (from original PhysioNetDataset)
        self.ecg_sequence_length = 18000  # Required length for model
        self.ecg_window_size = 256
        self.ecg_step = 256 - 32  # = 224
        self.spectrogram_length = 563
        
        # Spectrogram module (same as original)
        self.spectrogram_module = Spectrogram(
            n_fft=64, 
            win_length=64,
            hop_length=32,  # win_length // 2
            power=1, 
            normalized=True
        )
        
        # Load pretrained weights if provided
        if model_path is not None:
            self.load_pretrained(model_path, device)
        
        self.device = device
        self.to(device)  # Move entire wrapper (including spectrogram module) to device
        
    def load_pretrained(self, model_path, device='cpu'):
        """Load pretrained weights"""
        state_dict = torch.load(model_path, map_location=device, weights_only=False)
        
        # Remove 'module.' prefix if present (from DataParallel training)
        if any(k.startswith('module.') for k in state_dict.keys()):
            state_dict = {k.replace('module.', ''): v for k, v in state_dict.items()}
        
        self.model.load_state_dict(state_dict)
        print(f"Loaded pretrained weights from {model_path}")
        
    def preprocess(self, ecg_signal):
        """
        Preprocess ECG signal exactly like PhysioNetDataset
        Args:
            ecg_signal: (batch, 1, length) or (batch, length) ECG signal
        Returns:
            ecg_lead: (batch, num_windows, window_size) Unfolded ECG
            spectrogram: (batch, 1, freq, time) Log spectrogram
        """
        # Handle input shape
        if ecg_signal.dim() == 3:
            ecg_signal = ecg_signal.squeeze(1)  # (batch, length)
        
        batch_size = ecg_signal.shape[0]
        ecg_leads = []
        spectrograms = []
        
        for i in range(batch_size):
            ecg = ecg_signal[i]
            
            # Truncate to max sequence length
            ecg = ecg[:self.ecg_sequence_length]
            
            # Normalize signal
            ecg = (ecg - ecg.mean()) / (ecg.std() + 1e-08)
            
            # Compute spectrogram (Force CPU due to MPS FFT limitations)
            ecg_cpu = ecg.cpu()
            if self.spectrogram_module is not None:
                self.spectrogram_module.to('cpu')
                
            spec = self.spectrogram_module(ecg_cpu)
            spec = torch.log(spec.abs().clamp(min=1e-08))
            
            # Pad spectrogram to fixed length
            if spec.shape[-1] < self.spectrogram_length:
                spec = F.pad(spec, (0, self.spectrogram_length - spec.shape[-1]), value=0., mode="constant")
            spec = spec.permute(1, 0)  # (time, freq) -> (freq, time) after permute becomes (time, freq)
            
            # Move spec back to device if needed (will happen at stack)

            
            # Pad ECG to fixed length
            if ecg.shape[0] < self.ecg_sequence_length:
                ecg = F.pad(ecg, (0, self.ecg_sequence_length - ecg.shape[0]), value=0., mode="constant")
            
            # Unfold ECG lead
            ecg_unfolded = ecg.unfold(dimension=-1, size=self.ecg_window_size, step=self.ecg_step)
            
            ecg_leads.append(ecg_unfolded)
            spectrograms.append(spec.unsqueeze(0))  # Add channel dim
        
        # Ensure output is on the same device as the model parameters
        device = next(self.model.parameters()).device
        ecg_leads = torch.stack(ecg_leads).to(device)
        spectrograms = torch.stack(spectrograms).to(device)
        
        return ecg_leads.float(), spectrograms.float()
    
    def forward(self, ecg_signal, spectrogram=None):
        """
        Forward pass
        Args:
            ecg_signal: (batch, 1, length) or (batch, length) ECG signal
            spectrogram: If None, computed from ecg_signal
        Returns:
            (batch, 4) class logits
        """
        if spectrogram is None:
            ecg_lead, spectrogram = self.preprocess(ecg_signal)
        else:
            ecg_lead = ecg_signal
            
        return self.model(ecg_lead, spectrogram)
    
    def predict(self, ecg_signal):
        """
        Predict class from ECG signal
        Args:
            ecg_signal: (batch, 1, length) or (batch, length) ECG signal
        Returns:
            predictions: (batch,) predicted class indices
            probabilities: (batch, 4) class probabilities
        """
        self.eval()
        with torch.no_grad():
            logits = self.forward(ecg_signal)
            probabilities = torch.softmax(logits, dim=-1)
            predictions = torch.argmax(logits, dim=-1)
        return predictions, probabilities


def get_pretrained_ecg_dualnet(model_size='S', device='cpu', model_path=None):
    """
    Get pretrained ECG-DualNet model
    
    Args:
        model_size: 'S', 'M', 'L', or 'XL'
        device: 'cpu', 'cuda', or 'mps'
        model_path: Optional path to pretrained weights (for standalone deployment)
    
    Returns:
        ECGDualNetWrapper instance with pretrained weights
    """
    if model_path is not None:
        # Direct path provided (for standalone EDGE deployment)
        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"Model weights not found at {model_path}")
    else:
        # Map model sizes to their paths (from physio_net_dataset - 4 class)
        model_paths = {
            'S': 'ecg_dualnet_repo/experiments/13_05_2021__01_37_34ECGCNN_S_physio_net_dataset/models/best_model.pt',
            'M': 'ecg_dualnet_repo/experiments/13_05_2021__02_06_41ECGCNN_M_physio_net_dataset/models/best_model.pt',
            'L': 'ecg_dualnet_repo/experiments/13_05_2021__13_54_12ECGCNN_L_physio_net_dataset/models/best_model.pt',
            'XL': 'ecg_dualnet_repo/experiments/13_05_2021__09_42_13ECGAttNet_XL_physio_net_dataset/models/best_model.pt',
        }
        
        if model_size not in model_paths:
            raise ValueError(f"Model size must be one of {list(model_paths.keys())}")
        
        # Get absolute path
        model_path = Path(__file__).parent.parent / model_paths[model_size]
        
        if not model_path.exists():
            raise FileNotFoundError(f"Model weights not found at {model_path}")
    
    # Create and return wrapper
    wrapper = ECGDualNetWrapper(model_path=str(model_path), model_size=model_size, device=device)
    return wrapper


# Class mapping for CINC 2017
CLASSES = ['N', 'O', 'A', '~']  # Normal, Other, AF, Noisy


if __name__ == '__main__':
    # Test the wrapper
    print("Testing ECG-DualNet wrapper...")
    model = get_pretrained_ecg_dualnet(model_size='S', device='cpu')
    print("✓ Model loaded!")
    
    # Test with random input (9000 samples like CINC 2017)
    x = torch.randn(1, 1, 9000)
    preds, probs = model.predict(x)
    print(f"✓ Prediction: {CLASSES[preds.item()]}")
    print(f"✓ Probabilities: {probs[0].numpy()}")
    print("✓ SUCCESS!")
