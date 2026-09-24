from dataclasses import dataclass

import numpy as np

from src.vision.detector import YOLODetector


@dataclass
class PersonDetection:
    bbox: tuple[float, float, float, float]
    conf: float
    class_name: str = "person"


class PersonDetector:
    """Wrapper to keep person detection interface stable for future model swaps."""

    def __init__(self, model_name: str = "yolo26n.pt", conf_thres: float = 0.35) -> None:
        self._detector = YOLODetector(model_name=model_name, conf_thres=conf_thres)

    def detect(self, frame: np.ndarray) -> list[PersonDetection]:
        dets = self._detector.detect_persons(frame)
        return [PersonDetection(bbox=d.box, conf=d.conf) for d in dets]
