from dataclasses import dataclass
from datetime import datetime

import numpy as np

from src.vision.detector import Detection, YOLODetector
from src.vision.tracker import PersonTracker as LegacyPersonTracker


@dataclass
class PersonTrackOut:
    local_track_id: int
    bbox: tuple[float, float, float, float]
    centroid: tuple[float, float]
    velocity: float
    conf: float
    ts: datetime


class PersonTracker:
    """Local per-camera person tracker. Uses ByteTrack with IOU fallback via existing tracker."""

    def __init__(self, use_bytetrack: bool = True, iou_thres: float = 0.5) -> None:
        self._tracker = LegacyPersonTracker(use_bytetrack=use_bytetrack, iou_thres=iou_thres)

    def update(
        self,
        frame: np.ndarray,
        detections: list[tuple[float, float, float, float, float]],
        detector: YOLODetector,
        ts: datetime,
    ) -> list[PersonTrackOut]:
        dets = [Detection(box=(x1, y1, x2, y2), cls="person", conf=conf) for x1, y1, x2, y2, conf in detections]
        tracks = self._tracker.track(frame=frame, detections=dets, detector=detector, ts=ts)
        return [
            PersonTrackOut(
                local_track_id=t.track_id,
                bbox=t.bbox,
                centroid=t.centroid,
                velocity=t.velocity,
                conf=t.conf,
                ts=t.last_seen_ts,
            )
            for t in tracks
        ]
