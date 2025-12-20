"""
Model Definitions for EDGE Inference.
Standalone copies of PyTorch model architectures for loading trained weights.

These match the architectures in ML/models/*.py exactly.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

# Constants
NUM_CLASSES = 4
FIXED_LENGTH_SAMPLES = 9000  # 30 seconds at 300Hz


class BaseModel(nn.Module):
    """Base class for all models."""
    pass


# ===================== LIGHTWEIGHT MODELS =====================

class TinyCNN(BaseModel):
    """Ultra-lightweight CNN for fast inference."""
    def __init__(self, num_classes=NUM_CLASSES):
        super().__init__()
        self.conv1 = nn.Conv1d(1, 8, kernel_size=7, stride=4, padding=3)
        self.bn1 = nn.BatchNorm1d(8)
        self.pool1 = nn.MaxPool1d(4)
        self.conv2 = nn.Conv1d(8, 16, kernel_size=5, stride=2, padding=2)
        self.bn2 = nn.BatchNorm1d(16)
        self.pool2 = nn.MaxPool1d(4)
        self.gap = nn.AdaptiveAvgPool1d(1)
        self.fc = nn.Linear(16, num_classes)
        self.dropout = nn.Dropout(0.2)
    
    def forward(self, x):
        x = self.pool1(F.relu(self.bn1(self.conv1(x))))
        x = self.pool2(F.relu(self.bn2(self.conv2(x))))
        x = self.dropout(x)
        x = self.gap(x)
        x = x.flatten(1)
        return self.fc(x)


class MLP(BaseModel):
    """Multi-Layer Perceptron."""
    def __init__(self, input_size=900, num_classes=NUM_CLASSES):
        super().__init__()
        self.subsample_rate = 10
        self.fc1 = nn.Linear(input_size, 128)
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, num_classes)
        self.dropout = nn.Dropout(0.3)
    
    def forward(self, x):
        x = x[:, :, ::self.subsample_rate]
        x = x.flatten(1)
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        x = F.relu(self.fc2(x))
        x = self.dropout(x)
        return self.fc3(x)


class SimpleCNN(BaseModel):
    """Simple 4-layer CNN."""
    def __init__(self, num_classes=NUM_CLASSES):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv1d(1, 16, kernel_size=5, stride=1, padding=2),
            nn.BatchNorm1d(16),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(16, 32, kernel_size=5, stride=1, padding=2),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(32, 64, kernel_size=5, stride=1, padding=2),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(64, 128, kernel_size=5, stride=1, padding=2),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1)
        )
        self.classifier = nn.Linear(128, num_classes)

    def forward(self, x):
        x = self.features(x)
        x = x.flatten(1)
        return self.classifier(x)


class LSTMModel(BaseModel):
    """Bidirectional LSTM."""
    def __init__(self, num_classes=NUM_CLASSES, hidden_size=64, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_size=1, hidden_size=hidden_size, num_layers=num_layers, 
                           batch_first=True, bidirectional=True)
        self.fc = nn.Linear(hidden_size * 2, num_classes)

    def forward(self, x):
        x = x.permute(0, 2, 1)
        out, _ = self.lstm(x)
        out = out[:, -1, :]
        return self.fc(out)


# ===================== CNN-RNN MODELS =====================

class CRNN(BaseModel):
    """CNN + Bidirectional LSTM (CINC 2017 style)."""
    def __init__(self, num_classes=NUM_CLASSES):
        super().__init__()
        self.cnn = nn.Sequential(
            nn.Conv1d(1, 64, kernel_size=5, padding=2), nn.BatchNorm1d(64), nn.ReLU(), nn.MaxPool1d(2),
            nn.Conv1d(64, 128, kernel_size=5, padding=2), nn.BatchNorm1d(128), nn.ReLU(), nn.MaxPool1d(2),
            nn.Conv1d(128, 256, kernel_size=5, padding=2), nn.BatchNorm1d(256), nn.ReLU(), nn.MaxPool1d(2),
            nn.Conv1d(256, 512, kernel_size=5, padding=2), nn.BatchNorm1d(512), nn.ReLU(), nn.MaxPool1d(2)
        )
        self.lstm = nn.LSTM(input_size=512, hidden_size=128, num_layers=2, batch_first=True, bidirectional=True)
        self.dropout = nn.Dropout(0.3)
        self.fc = nn.Linear(256, num_classes)

    def forward(self, x):
        features = self.cnn(x)
        features = features.permute(0, 2, 1)
        self.lstm.flatten_parameters()
        output, _ = self.lstm(features)
        output = torch.mean(output, dim=1)
        output = self.dropout(output)
        return self.fc(output)


# ===================== RESNET MODELS =====================

class ResBlock(nn.Module):
    """Residual block for ResNet1D."""
    def __init__(self, in_channels, out_channels, kernel_size, stride, padding, downsample=None):
        super().__init__()
        self.conv1 = nn.Conv1d(in_channels, out_channels, kernel_size=kernel_size, stride=stride, padding=padding, bias=False)
        self.bn1 = nn.BatchNorm1d(out_channels)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv1d(out_channels, out_channels, kernel_size=kernel_size, stride=1, padding=padding, bias=False)
        self.bn2 = nn.BatchNorm1d(out_channels)
        self.downsample = downsample

    def forward(self, x):
        identity = x
        if self.downsample is not None:
            identity = self.downsample(x)
        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)
        out = self.conv2(out)
        out = self.bn2(out)
        out += identity
        out = self.relu(out)
        return out


class ResNet1D(BaseModel):
    """ResNet-34 adapted for 1D ECG (Stanford style)."""
    def __init__(self, num_classes=NUM_CLASSES):
        super().__init__()
        self.in_channels = 64
        self.base_filters = 64
        self.kernel_size = 15
        self.padding = 7
        self.stride = 2
        
        self.conv1 = nn.Conv1d(1, self.base_filters, kernel_size=self.kernel_size, stride=self.stride, padding=self.padding, bias=False)
        self.bn1 = nn.BatchNorm1d(self.base_filters)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool1d(kernel_size=3, stride=2, padding=1)
        
        self.layer1 = self._make_layer(self.base_filters, 3, stride=1)
        self.layer2 = self._make_layer(self.base_filters * 2, 4, stride=2)
        self.layer3 = self._make_layer(self.base_filters * 4, 6, stride=2)
        self.layer4 = self._make_layer(self.base_filters * 8, 3, stride=2)
        
        self.avgpool = nn.AdaptiveAvgPool1d(1)
        self.fc = nn.Linear(self.base_filters * 8, num_classes)

    def _make_layer(self, out_channels, blocks, stride):
        downsample = None
        if stride != 1 or self.in_channels != out_channels:
            downsample = nn.Sequential(
                nn.Conv1d(self.in_channels, out_channels, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm1d(out_channels)
            )
        layers = []
        layers.append(ResBlock(self.in_channels, out_channels, self.kernel_size, stride, self.padding, downsample))
        self.in_channels = out_channels
        for _ in range(1, blocks):
            layers.append(ResBlock(self.in_channels, out_channels, self.kernel_size, 1, self.padding))
        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        x = self.avgpool(x)
        x = x.flatten(1)
        return self.fc(x)


# ===================== ATTENTION MODELS =====================

class Attention(nn.Module):
    """Attention mechanism for CNNBiLSTMAttention."""
    def __init__(self, hidden_size):
        super().__init__()
        self.attention = nn.Linear(hidden_size, 1)
    
    def forward(self, lstm_output):
        attention_weights = F.softmax(self.attention(lstm_output), dim=1)
        context = torch.sum(attention_weights * lstm_output, dim=1)
        return context, attention_weights


class CNNBiLSTMAttention(BaseModel):
    """CNN-BiLSTM with Attention mechanism."""
    def __init__(self, num_classes=NUM_CLASSES):
        super().__init__()
        self.conv1 = nn.Conv1d(1, 32, kernel_size=5, stride=1, padding=2)
        self.bn1 = nn.BatchNorm1d(32)
        self.pool1 = nn.MaxPool1d(2)
        self.conv2 = nn.Conv1d(32, 64, kernel_size=5, stride=1, padding=2)
        self.bn2 = nn.BatchNorm1d(64)
        self.pool2 = nn.MaxPool1d(2)
        self.conv3 = nn.Conv1d(64, 128, kernel_size=3, stride=1, padding=1)
        self.bn3 = nn.BatchNorm1d(128)
        self.pool3 = nn.MaxPool1d(2)
        self.conv4 = nn.Conv1d(128, 256, kernel_size=3, stride=1, padding=1)
        self.bn4 = nn.BatchNorm1d(256)
        self.pool4 = nn.MaxPool1d(2)
        self.dropout = nn.Dropout(0.3)
        
        self.lstm_hidden = 128
        self.lstm = nn.LSTM(input_size=256, hidden_size=self.lstm_hidden, num_layers=2,
                           batch_first=True, bidirectional=True, dropout=0.3)
        self.attention = Attention(self.lstm_hidden * 2)
        self.fc1 = nn.Linear(self.lstm_hidden * 2, 64)
        self.fc2 = nn.Linear(64, num_classes)
    
    def forward(self, x):
        x = self.pool1(F.relu(self.bn1(self.conv1(x))))
        x = self.pool2(F.relu(self.bn2(self.conv2(x))))
        x = self.pool3(F.relu(self.bn3(self.conv3(x))))
        x = self.pool4(F.relu(self.bn4(self.conv4(x))))
        x = self.dropout(x)
        x = x.permute(0, 2, 1)
        lstm_out, _ = self.lstm(x)
        context, _ = self.attention(lstm_out)
        x = F.relu(self.fc1(context))
        x = self.dropout(x)
        x = self.fc2(x)
        return x


# ===================== ENCASE/NET1D =====================

class Net1DBlock(nn.Module):
    """Residual block for Net1D/ENCASE."""
    def __init__(self, in_channels, out_channels, kernel_size, stride, padding, downsample=None):
        super().__init__()
        self.conv1 = nn.Conv1d(in_channels, out_channels, kernel_size=kernel_size, stride=stride, padding=padding, bias=False)
        self.bn1 = nn.BatchNorm1d(out_channels)
        self.relu = nn.ReLU(inplace=True)
        self.dropout = nn.Dropout(p=0.2)
        self.conv2 = nn.Conv1d(out_channels, out_channels, kernel_size=kernel_size, stride=1, padding=padding, bias=False)
        self.bn2 = nn.BatchNorm1d(out_channels)
        self.downsample = downsample

    def forward(self, x):
        identity = x
        if self.downsample is not None:
            identity = self.downsample(x)
        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)
        out = self.dropout(out)
        out = self.conv2(out)
        out = self.bn2(out)
        if out.shape[-1] != identity.shape[-1]:
            diff = out.shape[-1] - identity.shape[-1]
            if diff > 0:
                out = out[..., :-diff]
            else:
                identity = F.pad(identity, (0, -diff))
        out += identity
        out = self.relu(out)
        return out


class Net1D(BaseModel):
    """Net1D / ENCASE architecture (CINC 2017 Winner)."""
    def __init__(self, num_classes=NUM_CLASSES):
        super().__init__()
        self.in_channels = 64
        self.kernel_size = 16
        self.padding = 8
        self.stride = 2
        
        self.conv1 = nn.Conv1d(1, 64, kernel_size=self.kernel_size, stride=2, padding=self.padding, bias=False)
        self.bn1 = nn.BatchNorm1d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool1d(kernel_size=3, stride=2, padding=1)
        
        self.layer1 = self._make_layer(64, 2, stride=1)
        self.layer2 = self._make_layer(160, 2, stride=2)
        self.layer3 = self._make_layer(160, 2, stride=2)
        self.layer4 = self._make_layer(400, 3, stride=2)
        self.layer5 = self._make_layer(400, 3, stride=2)
        self.layer6 = self._make_layer(1024, 4, stride=2)
        self.layer7 = self._make_layer(1024, 4, stride=2)
        
        self.avgpool = nn.AdaptiveAvgPool1d(1)
        self.fc = nn.Linear(1024, num_classes)

    def _make_layer(self, out_channels, blocks, stride):
        downsample = None
        if stride != 1 or self.in_channels != out_channels:
            downsample = nn.Sequential(
                nn.Conv1d(self.in_channels, out_channels, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm1d(out_channels)
            )
        layers = []
        layers.append(Net1DBlock(self.in_channels, out_channels, self.kernel_size, stride, self.padding, downsample))
        self.in_channels = out_channels
        for _ in range(1, blocks):
            layers.append(Net1DBlock(self.in_channels, out_channels, self.kernel_size, 1, self.padding))
        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        x = self.layer5(x)
        x = self.layer6(x)
        x = self.layer7(x)
        x = self.avgpool(x)
        x = x.flatten(1)
        return self.fc(x)


# Model name to class mapping
MODEL_CLASSES = {
    'tiny': TinyCNN,
    'tinycnn': TinyCNN,
    'mlp': MLP,
    'simple': SimpleCNN,
    'simplecnn': SimpleCNN,
    'cnn': SimpleCNN,  # Generic CNN uses SimpleCNN architecture
    'lstm': LSTMModel,
    'crnn': CRNN,
    'resnet': ResNet1D,
    'resnet1d': ResNet1D,
    'attention': CNNBiLSTMAttention,
    'bilstm': CNNBiLSTMAttention,
    'net1d': Net1D,
    'encase': Net1D,
    'ecg_dualnet': None,  # Special handling - loaded from ML directory
}
