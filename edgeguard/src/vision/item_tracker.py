from dataclasses import dataclass
from datetime import datetime

from src.vision.item_detector import ItemDetection


@dataclass
class ItemTrack:
    global_item_id: str
    bbox: tuple[float, float, float, float]
    cls: str
    conf: float
    owner_customer_id: str | None = None
    status: str = "ON_SHELF"
    last_seen_ts: datetime | None = None
    missing_frames: int = 0
    frames_visible: int = 0  # stability counter — filter single-frame noise


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    denom = area_a + area_b - inter
    return inter / denom if denom > 0.0 else 0.0


class ItemTracker:
    """IOU-based item tracker with aging and disappeared-track output.

    disappeared_tracks returned by update() are the key signal for
    pick-confirmation: an item that vanished while a person's wrist was
    nearby was almost certainly picked up.
    """

    MIN_VISIBLE_FRAMES = 3   # ignore single-frame detection blips
    MAX_MISSING_FRAMES = 12  # ~1 s at 12 FPS before declaring disappeared

    def __init__(self, iou_thres: float = 0.4) -> None:
        self.iou_thres = iou_thres
        self.tracks: dict[str, ItemTrack] = {}
        self._next_id = 1

    def update(
        self,
        detections: list[ItemDetection],
        ts: datetime,
    ) -> tuple[list[ItemTrack], list[ItemTrack]]:
        """Return (active_tracks, disappeared_tracks).

        disappeared_tracks contains only stable tracks (>= MIN_VISIBLE_FRAMES)
        that have exceeded MAX_MISSING_FRAMES — these are genuine disappearances,
        not detector noise.
        """
        existing_ids = list(self.tracks.keys())
        matched_det_idx: set[int] = set()
        matched_track_ids: set[str] = set()

        # Greedy IOU assignment — match each existing track to best detection
        for tid in existing_ids:
            track = self.tracks[tid]
            best_iou = self.iou_thres
            best_det_idx = -1
            for i, det in enumerate(detections):
                if i in matched_det_idx:
                    continue
                score = _iou(det.bbox, track.bbox)
                if score > best_iou:
                    best_iou = score
                    best_det_idx = i
            if best_det_idx >= 0:
                det = detections[best_det_idx]
                track.bbox = det.bbox
                track.conf = det.conf
                track.missing_frames = 0
                track.last_seen_ts = ts
                track.frames_visible += 1
                matched_det_idx.add(best_det_idx)
                matched_track_ids.add(tid)

        # Create new tracks for unmatched detections
        for i, det in enumerate(detections):
            if i not in matched_det_idx:
                new_id = f"item-{self._next_id}"
                self._next_id += 1
                self.tracks[new_id] = ItemTrack(
                    global_item_id=new_id,
                    bbox=det.bbox,
                    cls=det.cls,
                    conf=det.conf,
                    last_seen_ts=ts,
                    frames_visible=1,
                )

        # Age unmatched existing tracks; harvest disappeared
        disappeared: list[ItemTrack] = []
        to_delete: list[str] = []
        for tid in existing_ids:
            if tid in matched_track_ids:
                continue
            track = self.tracks[tid]
            track.missing_frames += 1
            if track.missing_frames > self.MAX_MISSING_FRAMES:
                if track.frames_visible >= self.MIN_VISIBLE_FRAMES:
                    disappeared.append(track)
                to_delete.append(tid)

        for tid in to_delete:
            self.tracks.pop(tid, None)

        return list(self.tracks.values()), disappeared
