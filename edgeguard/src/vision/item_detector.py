from dataclasses import dataclass

import numpy as np

from src.vision.detector import Detection, YOLODetector


@dataclass
class ItemDetection:
    bbox: tuple[float, float, float, float]
    cls: str
    conf: float


class ItemDetector:
    """Wraps the shared YOLODetector to surface retail-class items.

    Shares the already-loaded YOLO model so there is zero extra GPU cost —
    YOLODetector.detect_all() splits a single inference into persons and items.
    """

    def __init__(self, detector: YOLODetector | None = None, enabled: bool = True) -> None:
        self.enabled = enabled
        self._detector = detector

    def set_detector(self, detector: YOLODetector) -> None:
        self._detector = detector

    def detect(self, frame: np.ndarray) -> list[ItemDetection]:
        if not self.enabled or self._detector is None:
            return []
        raw: list[Detection] = self._detector.detect_items(frame)
        return [ItemDetection(bbox=d.box, cls=d.cls, conf=d.conf) for d in raw]

    def from_cached(self, cached_items: list[Detection]) -> list[ItemDetection]:
        """Convert pre-computed item detections (from detect_all) without re-inferring."""
        if not self.enabled:
            return []
        return [ItemDetection(bbox=d.box, cls=d.cls, conf=d.conf) for d in cached_items]
