from dataclasses import dataclass
from datetime import datetime

import numpy as np

from src.vision.tracker import PersonTrack

try:
    import torch
    from ultralytics import YOLO
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    YOLO = None


@dataclass
class PoseKeypoints:
    keypoints: list[tuple[float, float, float]]
    left_wrist: tuple[float, float] | None
    right_wrist: tuple[float, float] | None
    hip_center: tuple[float, float] | None
    left_wrist_conf: float = 0.0
    right_wrist_conf: float = 0.0
    available: bool = False
    hand_to_hip_distance: float | None = None
    hand_speed: float | None = None
    ts: datetime | None = None


class PoseEstimator:
    def __init__(self, model_name: str = "yolo26n-pose.pt", conf_thres: float = 0.2) -> None:
        self.conf_thres = conf_thres
        self._enabled = YOLO is not None
        self._model = None
        if YOLO is not None:
            try:
                if torch is not None:
                    if torch.cuda.is_available():
                        device = "cuda"
                    elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                        device = "mps"
                    else:
                        device = "cpu"
                else:
                    device = "cpu"
                self._model = YOLO(model_name)
                self._model.to(device)
                self._device = device
            except Exception:
                self._enabled = False
        self._last_hand_positions: dict[int, tuple[datetime, tuple[float, float]]] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled and self._model is not None

    @staticmethod
    def _extract_point(kpts: np.ndarray, idx: int, min_conf: float) -> tuple[float, float] | None:
        if idx >= len(kpts):
            return None
        x, y, c = kpts[idx].tolist()
        if c < min_conf:
            return None
        return (float(x), float(y))

    def _derive(self, track_id: int, kpts: np.ndarray, ts: datetime) -> PoseKeypoints:
        left_wrist = self._extract_point(kpts, 9, self.conf_thres)
        right_wrist = self._extract_point(kpts, 10, self.conf_thres)
        left_hip = self._extract_point(kpts, 11, self.conf_thres)
        right_hip = self._extract_point(kpts, 12, self.conf_thres)

        hip_center: tuple[float, float] | None = None
        if left_hip and right_hip:
            hip_center = ((left_hip[0] + right_hip[0]) / 2.0, (left_hip[1] + right_hip[1]) / 2.0)
        elif left_hip:
            hip_center = left_hip
        elif right_hip:
            hip_center = right_hip

        hand_candidates = [p for p in [left_wrist, right_wrist] if p is not None]
        hand_center = hand_candidates[0] if hand_candidates else None
        if len(hand_candidates) == 2:
            hand_center = ((hand_candidates[0][0] + hand_candidates[1][0]) / 2.0, (hand_candidates[0][1] + hand_candidates[1][1]) / 2.0)

        hand_to_hip = None
        if hand_center and hip_center:
            hand_to_hip = ((hand_center[0] - hip_center[0]) ** 2 + (hand_center[1] - hip_center[1]) ** 2) ** 0.5

        hand_speed = None
        if hand_center:
            prev = self._last_hand_positions.get(track_id)
            if prev:
                prev_ts, prev_pos = prev
                dt = max(1e-6, (ts - prev_ts).total_seconds())
                hand_speed = (((hand_center[0] - prev_pos[0]) ** 2 + (hand_center[1] - prev_pos[1]) ** 2) ** 0.5) / dt
            self._last_hand_positions[track_id] = (ts, hand_center)

        return PoseKeypoints(
            keypoints=[(float(x), float(y), float(c)) for x, y, c in kpts.tolist()],
            left_wrist=left_wrist,
            right_wrist=right_wrist,
            hip_center=hip_center,
            left_wrist_conf=float(kpts[9][2]) if len(kpts) > 9 else 0.0,
            right_wrist_conf=float(kpts[10][2]) if len(kpts) > 10 else 0.0,
            available=any(v is not None for v in [left_wrist, right_wrist, hip_center]),
            hand_to_hip_distance=hand_to_hip,
            hand_speed=hand_speed,
            ts=ts,
        )

    def estimate(self, frame: np.ndarray, tracks: list[PersonTrack], ts: datetime) -> dict[int, PoseKeypoints]:
        results: dict[int, PoseKeypoints] = {}

        _empty = PoseKeypoints(
            keypoints=[],
            left_wrist=None,
            right_wrist=None,
            hip_center=None,
            available=False,
            ts=ts,
        )

        if not self.enabled:
            for track in tracks:
                results[track.track_id] = _empty
            return results

        frame_h, frame_w = frame.shape[:2]

        # Collect all valid crops in one pass — Fix 2: batch inference
        crops: list[np.ndarray] = []
        track_ids: list[int] = []
        offsets: list[tuple[int, int]] = []

        for track in tracks:
            x1, y1, x2, y2 = [int(v) for v in track.bbox]
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(frame_w - 1, x2)
            y2 = min(frame_h - 1, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            crops.append(crop)
            track_ids.append(track.track_id)
            offsets.append((x1, y1))

        if crops:
            try:
                preds = self._model.predict(crops, conf=self.conf_thres, verbose=False)
                for pred, tid, (ox, oy) in zip(preds, track_ids, offsets):
                    if pred.keypoints is None or len(pred.keypoints) == 0:
                        continue
                    kpts = pred.keypoints.data[0].cpu().numpy()
                    kpts[:, 0] += ox
                    kpts[:, 1] += oy
                    results[tid] = self._derive(tid, kpts, ts)
            except Exception:
                pass

        for track in tracks:
            results.setdefault(track.track_id, _empty)
        return results
