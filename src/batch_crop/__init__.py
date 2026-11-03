"""batch-crop: draw a crop box once and crop a whole folder of images the same way."""

__version__ = "1.0.0"

from .engine import CropOp, CropSpec, Mismatch, operation_for, plan, run
from .geometry import Anchor, AspectRatio, Box, Size

__all__ = [
    "Anchor",
    "AspectRatio",
    "Box",
    "CropOp",
    "CropSpec",
    "Mismatch",
    "Size",
    "__version__",
    "operation_for",
    "plan",
    "run",
]
