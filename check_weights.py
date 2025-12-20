import torch
try:
    state_dict = torch.load("ML/models/saved/resnet1d_hsd1503.pth", map_location='cpu')
    print("Loaded state dict.")
    for key in list(state_dict.keys())[:5]:
        print(f"{key}: {state_dict[key].shape}")
        
    # Check first block conv
    # The key name depends on the model structure.
    # Looking at resnet1d.py, it has self.first_block_conv which is MyConv1dPadSame
    # MyConv1dPadSame has self.conv
    keys = [k for k in state_dict.keys() if "first_block_conv" in k]
    print("First block keys:", keys)
    
except Exception as e:
    print(f"Error: {e}")
