import sys
import os
import torch

# Add path to ML folder
sys.path.append(os.getcwd())

print("Testing Model Loading...")

try:
    # 1. Test EDGE Model Definition Import
    print("\n[EDGE] Importing HSDResNet1D from processors/resnet1d_hsd.py...")
    from EDGE.processors.resnet1d_hsd import HSDResNet1D as EDGEResnet
    model_edge = EDGEResnet(in_channels=1, base_filters=128, kernel_size=16, stride=2, groups=1, n_block=48, n_classes=4)
    print("SUCCESS: Instantiated EDGE HSDResNet1D")
    
    # 2. Test ML Model Definition Import
    print("\n[ML] Importing HSDResNet1D from ML/models/resnet1d_hsd.py...")
    from ML.models.resnet1d_hsd import HSDResNet1D as MLResnet
    model_ml = MLResnet(in_channels=1, base_filters=128, kernel_size=16, stride=2, groups=1, n_block=48, n_classes=4)
    print("SUCCESS: Instantiated ML HSDResNet1D")
    
    # 3. Test Loading Weights
    weights_path = "ML/models/saved/resnet1d_hsd1503.pth"
    if os.path.exists(weights_path):
        print(f"\nLoading weights from {weights_path}...")
        state_dict = torch.load(weights_path, map_location='cpu')
        model_ml.load_state_dict(state_dict)
        print("SUCCESS: Loaded weights into ML model")
    else:
        print(f"FAILURE: Weights not found at {weights_path}")
        
    print("\nALL CHECKS PASSED.")

except Exception as e:
    print(f"\nFAILURE: {e}")
    import traceback
    traceback.print_exc()
