"""PAde activation unit for ECG-DualNet."""
from .pytorch_impl import PADEACTIVATION_Function_based

# Provide PAU function for compatibility
def PAU():
    """Pure PyTorch PAU activation - no CUDA required."""
    return PADEACTIVATION_Function_based(init_coefficients="pade_optimized_leakyrelu")

__all__ = ['PAU', 'PADEACTIVATION_Function_based']
