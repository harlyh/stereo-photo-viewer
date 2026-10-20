"""Depth Anything V2 Small, using the same model as 2D3D's fast profile."""

import numpy as np
import torch

MODEL = "depth-anything/Depth-Anything-V2-Small-hf"


class DepthEstimator:
    def __init__(self):
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        self.device = ("cuda" if torch.cuda.is_available() else
                       "mps" if torch.backends.mps.is_available() else "cpu")
        self.processor = AutoImageProcessor.from_pretrained(MODEL)
        self.model = AutoModelForDepthEstimation.from_pretrained(MODEL).to(self.device).eval()
        # Eager inference avoids torch.compile/Triton setup and startup costs
        # for an interactive photo viewer, including on Windows.

    @torch.inference_mode()
    def predict(self, rgb):
        inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)
        depth = self.model(**inputs).predicted_depth
        depth = torch.nn.functional.interpolate(
            depth.unsqueeze(1), size=rgb.shape[:2], mode="bicubic", align_corners=False
        )[0, 0].float().cpu().numpy()
        low, high = float(depth.min()), float(depth.max())
        if high <= low:
            return np.zeros_like(depth, dtype=np.float32)
        return ((depth - low) / (high - low)).astype(np.float32)
