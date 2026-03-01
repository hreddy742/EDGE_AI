from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
import uuid

import numpy as np


@dataclass
class GlobalCustomer:
    global_customer_id: str
    current_camera_id: str
    per_camera_track_ids: dict[str, int] = field(default_factory=dict)
    last_seen_time: datetime | None = None
    appearance_embedding: list[float] = field(default_factory=list)
    last_height_px: float | None = None
    risk_score_current: float = 0.0


class GlobalIdentityResolver:
    def __init__(
        self,
        match_threshold: float = 0.72,
        handoff_window_sec: float = 12.0,
        adjacency: dict[str, list[str]] | None = None,
        enable_cross_camera_match: bool = False,
    ) -> None:
        self.match_threshold = match_threshold
        self.handoff_window_sec = handoff_window_sec
        self.adjacency = adjacency or {}
        self.enable_cross_camera_match = enable_cross_camera_match
        self.customers: dict[str, GlobalCustomer] = {}
        self.local_to_global: dict[tuple[str, int], str] = {}
        self._lock = Lock()

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom <= 1e-8:
            return 0.0
        return float(np.dot(va, vb) / denom)

    def _transition_score(self, from_cam: str, to_cam: str) -> float:
        if from_cam == to_cam:
            return 1.0
        if to_cam in self.adjacency.get(from_cam, []):
            return 0.9
        if from_cam in self.adjacency.get(to_cam, []):
            return 0.85
        return 0.2

    def _time_score(self, prev_ts: datetime | None, ts: datetime) -> float:
        if prev_ts is None:
            return 0.0
        dt = max(0.0, (ts - prev_ts).total_seconds())
        if dt > self.handoff_window_sec:
            return 0.0
        return max(0.0, 1.0 - dt / self.handoff_window_sec)

    def match_or_create(
        self,
        camera_id: str,
        local_track_id: int,
        embedding: list[float],
        ts: datetime,
        candidate_scores: list[tuple[str, float]] | None = None,
        height_px: float | None = None,
    ) -> str:
        with self._lock:
            key = (camera_id, local_track_id)
            existing = self.local_to_global.get(key)
            if existing and existing in self.customers:
                c = self.customers[existing]
                c.current_camera_id = camera_id
                c.per_camera_track_ids[camera_id] = local_track_id
                c.last_seen_time = ts
                if embedding:
                    c.appearance_embedding = embedding
                if height_px is not None:
                    c.last_height_px = height_px
                return existing

            if not self.enable_cross_camera_match:
                new_id = f"CUST-{uuid.uuid4()}"
                self.customers[new_id] = GlobalCustomer(
                    global_customer_id=new_id,
                    current_camera_id=camera_id,
                    per_camera_track_ids={camera_id: local_track_id},
                    last_seen_time=ts,
                    appearance_embedding=embedding,
                    last_height_px=height_px,
                )
                self.local_to_global[key] = new_id
                return new_id

            best_customer_id = ""
            best_score = -1.0
            if candidate_scores:
                for customer_id, score in candidate_scores:
                    if score > best_score:
                        best_customer_id = customer_id
                        best_score = score
            else:
                for customer_id, c in self.customers.items():
                    if self._time_score(c.last_seen_time, ts) <= 0.0:
                        continue
                    emb_score = self._cosine(embedding, c.appearance_embedding)
                    t_score = self._time_score(c.last_seen_time, ts)
                    tr_score = self._transition_score(c.current_camera_id, camera_id)
                    size_score = 0.5
                    if height_px is not None and c.last_height_px is not None and c.last_height_px > 1e-6:
                        size_score = max(0.0, 1.0 - abs(height_px - c.last_height_px) / max(height_px, c.last_height_px))
                    score = 0.55 * emb_score + 0.20 * t_score + 0.15 * tr_score + 0.10 * size_score
                    if score > best_score:
                        best_customer_id = customer_id
                        best_score = score

            if best_customer_id and best_score >= self.match_threshold:
                c = self.customers[best_customer_id]
                c.current_camera_id = camera_id
                c.per_camera_track_ids[camera_id] = local_track_id
                c.last_seen_time = ts
                c.appearance_embedding = embedding or c.appearance_embedding
                c.last_height_px = height_px if height_px is not None else c.last_height_px
                self.local_to_global[key] = best_customer_id
                return best_customer_id

            new_id = f"CUST-{uuid.uuid4()}"
            self.customers[new_id] = GlobalCustomer(
                global_customer_id=new_id,
                current_camera_id=camera_id,
                per_camera_track_ids={camera_id: local_track_id},
                last_seen_time=ts,
                appearance_embedding=embedding,
                last_height_px=height_px,
            )
            self.local_to_global[key] = new_id
            return new_id
