from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class ReIDResult:
    embedding: list[float]
    quality: float


class ReIDEmbedder:
    """
    Experimental appearance descriptor.
    This currently computes a simple HSV histogram and is NOT a production-grade
    metric-learning ReID model (e.g., torchreid/FastReID).
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def embed(self, person_crop: np.ndarray) -> ReIDResult | None:
        if not self.enabled:
            return None
        if person_crop.size == 0:
            return None
        try:
            hsv = cv2.cvtColor(person_crop, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [24, 8], [0, 180, 0, 256]).flatten()
            if hist.size == 0:
                return None
            hist = hist.astype(np.float32)
            norm = float(np.linalg.norm(hist))
            if norm > 1e-8:
                hist /= norm
            quality = float(min(1.0, person_crop.shape[0] * person_crop.shape[1] / (180.0 * 80.0)))
            return ReIDResult(embedding=hist.tolist(), quality=quality)
        except Exception:
            return None

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom <= 1e-8:
            return 0.0
        return float(np.dot(va, vb) / denom)
