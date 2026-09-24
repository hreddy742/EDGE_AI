from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from src.core.logger import logger
from src.rules.zones import centroid_from_bbox
from src.vision.detector import Detection, YOLODetector


@dataclass
class PersonTrack:
    track_id: int
    bbox: tuple[float, float, float, float]
    conf: float
    centroid: tuple[float, float]
    velocity: float
    last_seen_ts: datetime


@dataclass
class TrackMemory:
    track_id: int
    centroid_history: deque[tuple[datetime, tuple[float, float]]] = field(default_factory=lambda: deque(maxlen=60))
    last_bbox: tuple[float, float, float, float] | None = None
    last_conf: float = 0.0
    last_seen_ts: datetime | None = None
    state: str = "BROWSING"
    risk_score: float = 0.0


def bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter = inter_w * inter_h

    area_a = max(0.0, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(0.0, (bx2 - bx1) * (by2 - by1))
    denom = area_a + area_b - inter
    if denom <= 0.0:
        return 0.0
    return inter / denom


class _IOUFallbackTracker:
    def __init__(self, iou_thres: float = 0.5, max_age_seconds: int = 2) -> None:
        self.iou_thres = iou_thres
        self.max_age_seconds = max_age_seconds
        self.next_track_id = 1
        self.state: dict[int, dict[str, object]] = {}

    def update(self, detections: list[Detection], ts: datetime) -> list[Detection]:
        assigned: set[int] = set()
        out: list[Detection] = []

        for det in detections:
            best_id: int | None = None
            best_iou = 0.0
            for track_id, value in self.state.items():
                if track_id in assigned:
                    continue
                box = value.get("bbox")
                if not isinstance(box, tuple):
                    continue
                iou = bbox_iou(det.box, box)
                if iou >= self.iou_thres and iou > best_iou:
                    best_iou = iou
                    best_id = track_id

            if best_id is None:
                best_id = self.next_track_id
                self.next_track_id += 1

            self.state[best_id] = {"bbox": det.box, "ts": ts}
            assigned.add(best_id)
            out.append(Detection(box=det.box, cls=det.cls, conf=det.conf, track_id=best_id))

        stale: list[int] = []
        for track_id, value in self.state.items():
            last_ts = value.get("ts")
            if isinstance(last_ts, datetime) and (ts - last_ts).total_seconds() > self.max_age_seconds:
                stale.append(track_id)
        for track_id in stale:
            self.state.pop(track_id, None)

        return out


class PersonTracker:
    def __init__(self, use_bytetrack: bool, iou_thres: float = 0.5) -> None:
        self.use_bytetrack = use_bytetrack
        self._bytetrack_disabled = False
        self._iou = _IOUFallbackTracker(iou_thres=iou_thres)
        self.memory: dict[int, TrackMemory] = {}

    def _velocity(self, memory: TrackMemory, ts: datetime, centroid: tuple[float, float]) -> float:
        if not memory.centroid_history:
            return 0.0
        prev_ts, prev = memory.centroid_history[-1]
        dt = max(1e-6, (ts - prev_ts).total_seconds())
        dx = centroid[0] - prev[0]
        dy = centroid[1] - prev[1]
        return ((dx * dx + dy * dy) ** 0.5) / dt

    def _update_memory(self, tracked: list[Detection], ts: datetime) -> list[PersonTrack]:
        output: list[PersonTrack] = []
        for det in tracked:
            if det.track_id is None:
                continue

            centroid = centroid_from_bbox(det.box)
            memory = self.memory.get(det.track_id)
            if memory is None:
                memory = TrackMemory(track_id=det.track_id)
                self.memory[det.track_id] = memory

            velocity = self._velocity(memory, ts, centroid)
            memory.centroid_history.append((ts, centroid))
            memory.last_bbox = det.box
            memory.last_conf = det.conf
            memory.last_seen_ts = ts

            output.append(
                PersonTrack(
                    track_id=det.track_id,
                    bbox=det.box,
                    conf=det.conf,
                    centroid=centroid,
                    velocity=velocity,
                    last_seen_ts=ts,
                )
            )

        return output

    def track(self, frame, detections: list[Detection], detector: YOLODetector, ts: datetime) -> list[PersonTrack]:
        tracked_dets: list[Detection] = []

        if self.use_bytetrack and not self._bytetrack_disabled:
            try:
                tracked_dets = detector.track_persons(frame)
                tracked_dets = [det for det in tracked_dets if det.track_id is not None]
            except Exception as exc:
                logger.warning(f"ByteTrack unavailable; switching to IOU fallback: {exc}")
                self._bytetrack_disabled = True

        if not tracked_dets:
            tracked_dets = self._iou.update(detections, ts)

        return self._update_memory(tracked_dets, ts)
