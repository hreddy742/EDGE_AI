from collections import deque
import csv
from datetime import datetime, timedelta
import os
from pathlib import Path
import queue
from threading import Event, Lock, Thread
import time
from typing import Any
import uuid

import cv2

from src.alerts.webhook import send_event_webhook
from src.core.config import Settings
from src.core.logger import logger
from src.evidence.clip_writer import ClipWriter
from src.fusion.global_identity import GlobalIdentityResolver
from src.rules.theft_fsm import TheftEvent, TheftRiskFSM, TheftSignal
from src.rules.risk import RiskEngine
from src.rules.theft_state_machine import StateEvent, TheftStateMachine
from src.rules.zones import ZoneConfig, is_point_in_zone, load_zone_config
from src.store import crud
from src.store.db import get_session_local, init_db, is_sqlite_corruption_error, recover_sqlite_database
from src.rules.association import confirm_pick_from_disappeared
from src.store.db_writer import DBWriteWorker
from src.video.frames import FrameSampler
from src.video.sources import RTSPSource, VideoFileSource
from src.vision.annotator import annotate_frame
from src.vision.detector import YOLODetector
from src.vision.item_detector import ItemDetector
from src.vision.item_tracker import ItemTracker
from src.vision.pose import PoseEstimator, PoseKeypoints
from src.vision.reid import ReIDEmbedder
from src.vision.tracker import PersonTracker


class PipelineRunner:
    def __init__(self, settings: Settings, identity_resolver: GlobalIdentityResolver | None = None) -> None:
        self.settings = settings
        self.identity_resolver = identity_resolver or GlobalIdentityResolver()
        self.stop_event = Event()
        self.thread: Thread | None = None
        self.latest_frame_bytes: bytes | None = None
        self.latest_frame_lock = Lock()
        self._last_jpeg_ts: float = 0.0
        self.recent_frames: deque[tuple[datetime, Any]] = deque(
            maxlen=max(
                30,
                settings.frame_fps * (settings.theft_clip_seconds_before + settings.theft_clip_seconds_after + 2),
            )
        )
        self.pending_clips: list[dict] = []
        self.debug_file: Path | None = None
        self.clip_writer = ClipWriter(output_dir=str(settings.clip_path))
        self.state_lock = Lock()
        self.item_state_machine = TheftStateMachine()
        self.item_risk = RiskEngine()
        self.reid = ReIDEmbedder(enabled=settings.cross_camera_reid_enabled)
        self.customer_first_seen: dict[str, datetime] = {}
        self.customer_evidence_links: dict[str, list[str]] = {}
        self.track_item_counter: dict[str, int] = {}
        self.track_active_items: dict[str, list[str]] = {}
        self.last_pick_ts: dict[str, datetime] = {}
        self.pick_history: dict[str, deque[datetime]] = {}
        self.customer_counter_mismatch_open: dict[str, bool] = {}
        self.customer_alert_state: dict[str, bool] = {}
        self.customer_prev_in_exit_zone: dict[str, bool] = {}
        self.counter_session_state: dict[str, dict] = {}
        self.counter_missing_persist_seconds = float(os.getenv("COUNTER_MISSING_BUFFER_SECONDS", "2.0"))
        self._zones_override: ZoneConfig | None = None
        self._zones_lock = Lock()

        # Background clip finalization thread (Fix 8)
        self._clip_finalize_queue: queue.Queue = queue.Queue()
        self._clip_thread: Thread | None = None

        # DB write worker — created in _run() after init_db() (Fix 3)
        self._db_writer: DBWriteWorker | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        # Start clip finalization background thread (Fix 8)
        self._clip_thread = Thread(target=self._clip_finalize_worker, name="clip-finalizer", daemon=True)
        self._clip_thread.start()
        self.thread = Thread(target=self._run, name="edgeguard-pipeline", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        # Signal clip thread to exit and wait
        if self._clip_thread and self._clip_thread.is_alive():
            self._clip_finalize_queue.put(None)
            self._clip_thread.join(timeout=30)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        # DB writer is stopped inside _run() finally block

    def get_latest_frame_bytes(self) -> bytes | None:
        with self.latest_frame_lock:
            return self.latest_frame_bytes

    def reconcile_pos(
        self,
        customer_id: str,
        paid_count: int | None = None,
        paid_item_ids: list[str] | None = None,
    ) -> dict:
        paid_item_ids = paid_item_ids or []
        now = datetime.utcnow()
        session_local = get_session_local()
        db = session_local()
        try:
            with self.state_lock:
                basket = self.item_state_machine.get_basket(customer_id)
                possessed = sorted(list(basket.inferred_total_possessed()))
                if paid_item_ids:
                    paid_set = set(paid_item_ids)
                else:
                    paid_set = set(possessed[: max(0, int(paid_count or 0))])
                unpaid = sorted(list(set(possessed) - paid_set))
                risk_score = self.item_risk.decay(customer_id, now, allow_decay=self._allow_risk_decay(customer_id))
                emitted_event_ids: list[str] = []

                if unpaid:
                    risk_score = self.item_risk.apply_delta(customer_id, 20.0, "CHECKOUT_MISMATCH", now, allow_decay=False)
                    ev = TheftEvent(
                        event_id="",
                        camera_id=self.settings.camera_id,
                        ts_start=now,
                        ts_trigger=now,
                        track_id=self._track_id_from_customer_id(customer_id),
                        event_type="CHECKOUT_MISMATCH",
                        risk_score_at_trigger=round(risk_score, 2),
                        snapshot_path=None,
                        short_explanation="POS paid items do not match possessed items.",
                        details={"customer_id": customer_id, "unpaid_item_ids": unpaid, "source": "pos_reconciliation"},
                    )
                    self._emit_event(db=db, event=ev, frame=None, contributing_signals=[])
                    emitted = crud.list_events(db=db, camera_id=self.settings.camera_id, event_type="CHECKOUT_MISMATCH", limit=1)
                    if emitted:
                        emitted_event_ids.append(emitted[0].event_id)
                else:
                    self.item_risk.apply_delta(customer_id, -10.0, "PAY_ALL", now, allow_decay=False)

                fake_track = type("TrackLike", (), {"centroid": (0.0, 0.0)})()
                self._upsert_customer_runtime(
                    db=db,
                    customer_id=customer_id,
                    track=fake_track,
                    ts=now,
                    risk_score=self.item_risk.decay(customer_id, now, allow_decay=self._allow_risk_decay(customer_id)),
                    zones=ZoneConfig(camera_id=self.settings.camera_id, zones={}),
                )

                return {
                    "customer_id": customer_id,
                    "paid_count": paid_count,
                    "paid_item_ids": paid_item_ids,
                    "unpaid_item_ids": unpaid,
                    "risk_score": self.item_risk.decay(customer_id, now, allow_decay=self._allow_risk_decay(customer_id)),
                    "emitted_event_ids": emitted_event_ids,
                }
        finally:
            db.close()

    def reconcile_counter(
        self,
        customer_id: str,
        presented_item_ids: list[str] | None = None,
        presented_unknown_count: int = 0,
    ) -> dict:
        now = datetime.utcnow()
        presented_item_ids = presented_item_ids or []
        session_local = get_session_local()
        db = session_local()
        try:
            with self.state_lock:
                basket = self.item_state_machine.get_basket(customer_id)
                expected_ids = set(basket.inferred_total_possessed())
                presented_ids = set(presented_item_ids)
                expected_count = len(expected_ids)
                presented_count = len(presented_ids) + max(0, int(presented_unknown_count))
                missing_known = sorted(list(expected_ids - presented_ids))
                missing_count = max(0, expected_count - presented_count)

                if missing_count > 0:
                    self.item_state_machine.set_mismatch_unresolved(customer_id, True)
                    self.item_risk.apply_delta(customer_id, 20.0, "COUNTER_MISMATCH", now, allow_decay=False)
                else:
                    self.item_state_machine.set_mismatch_unresolved(customer_id, False)
                    self.item_risk.apply_delta(customer_id, -10.0, "COUNTER_RECONCILED", now, allow_decay=False)

                risk_score = self.item_risk.decay(customer_id, now, allow_decay=self._allow_risk_decay(customer_id))
                fake_track = type("TrackLike", (), {"centroid": (0.0, 0.0)})()
                self._upsert_customer_runtime(
                    db=db,
                    customer_id=customer_id,
                    track=fake_track,
                    ts=now,
                    risk_score=risk_score,
                    zones=ZoneConfig(camera_id=self.settings.camera_id, zones={}),
                )
                return {
                    "customer_id": customer_id,
                    "missing_count": missing_count,
                    "missing_item_ids": missing_known[:missing_count] if missing_count else [],
                    "resolved": missing_count == 0,
                    "risk_score": risk_score,
                }
        finally:
            db.close()

    @staticmethod
    def _track_id_from_customer_id(customer_id: str) -> int:
        # Supports both legacy camera-scoped IDs (cam01:12) and global IDs (CUST-uuid).
        if ":" not in customer_id:
            return 0
        tail = customer_id.rsplit(":", maxsplit=1)[-1]
        try:
            return int(tail)
        except ValueError:
            return 0

    def update_zones(self, zones: dict[str, list[tuple[int, int]]]) -> None:
        normalized = {
            str(name): [(int(p[0]), int(p[1])) for p in pts]
            for name, pts in zones.items()
        }
        with self._zones_lock:
            self._zones_override = ZoneConfig(camera_id=self.settings.camera_id, zones=normalized)

    def get_zones(self) -> dict[str, list[tuple[int, int]]]:
        with self._zones_lock:
            if self._zones_override is None:
                return {}
            return {k: list(v) for k, v in self._zones_override.zones.items()}

    def _active_zones(self, loaded: ZoneConfig) -> ZoneConfig:
        with self._zones_lock:
            if self._zones_override is not None:
                return self._zones_override
        return loaded

    # ------------------------------------------------------------------
    # Frame encoding (Fix 5 — JPEG rate-limit)
    # ------------------------------------------------------------------

    def _set_latest_frame(self, frame) -> None:
        now = time.monotonic()
        min_interval = 1.0 / max(1, int(self.settings.stream_jpeg_max_fps))
        if now - self._last_jpeg_ts < min_interval:
            return
        self._last_jpeg_ts = now
        ok, encoded = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.settings.jpeg_quality]
        )
        if ok:
            with self.latest_frame_lock:
                self.latest_frame_bytes = encoded.tobytes()

    # ------------------------------------------------------------------
    # Source builder
    # ------------------------------------------------------------------

    def _build_source(self):
        if self.settings.video_source_type == "rtsp":
            return RTSPSource(
                self.settings.rtsp_url,
                stop_event=self.stop_event,
                transport=self.settings.rtsp_transport,
                open_timeout_ms=self.settings.rtsp_open_timeout_ms,
                read_timeout_ms=self.settings.rtsp_read_timeout_ms,
                buffer_size=self.settings.rtsp_buffer_size,
                ffmpeg_options=self.settings.rtsp_ffmpeg_options,
            )
        return VideoFileSource(str(self.settings.video_path), loop=True)

    # ------------------------------------------------------------------
    # Snapshot / clip helpers
    # ------------------------------------------------------------------

    def _save_snapshot(self, frame, event_id: str) -> str | None:
        self.settings.snapshot_path.mkdir(parents=True, exist_ok=True)
        out_path = self.settings.snapshot_path / f"{event_id}.jpg"
        ok = cv2.imwrite(str(out_path), frame)
        if not ok:
            return None
        return str(out_path)

    def _start_event_clip(self, event_id: str, track_id: int, ts: datetime, db) -> tuple[str, str]:
        clip_id = str(uuid.uuid4())
        clip_name = f"{self.settings.camera_id}_{event_id}_{clip_id}"
        out_path = self.settings.clip_path / f"{clip_name}.mp4"
        pre_start = ts - timedelta(seconds=self.settings.theft_clip_seconds_before)
        # recent_frames now stores RAW frames (Fix 4)
        pre_frames = [frame.copy() for frame_ts, frame in self.recent_frames if frame_ts >= pre_start]
        self.pending_clips.append(
            {
                "clip_id": clip_id,
                "event_id": event_id,
                "end_ts": ts + timedelta(seconds=self.settings.theft_clip_seconds_after),
                "frames": pre_frames,
                "clip_name": clip_name,
            }
        )
        crud.create_clip(
            db=db,
            clip_id=clip_id,
            event_id=event_id,
            camera_id=self.settings.camera_id,
            track_id=track_id,
            ts_start=pre_start,
            ts_end=ts + timedelta(seconds=self.settings.theft_clip_seconds_after),
            status="TEMP",
            processing_status="PENDING",
            clip_path=str(out_path),
        )
        return clip_id, str(out_path)

    def _finalize_clip(self, clip: dict) -> None:
        """Write frames to disk and update DB via the writer (no db session needed)."""
        frames = clip.get("frames", [])
        if not frames:
            if self._db_writer:
                self._db_writer.put("clip_update", {
                    "clip_id": clip["clip_id"],
                    "processing_status": "FAILED",
                    "clip_path": None,
                })
            return
        clip_path = self.clip_writer.write_frames(
            clip_name=clip["clip_name"],
            frames=frames,
            fps=max(1, self.settings.frame_fps),
        )
        if self._db_writer:
            self._db_writer.put("clip_update", {
                "clip_id": clip["clip_id"],
                "processing_status": "READY" if clip_path else "FAILED",
                "clip_path": clip_path,
            })

    def _clip_finalize_worker(self) -> None:
        """Drain the clip finalization queue in a dedicated thread (Fix 8)."""
        while True:
            item = self._clip_finalize_queue.get()
            if item is None:
                self._clip_finalize_queue.task_done()
                break
            try:
                self._finalize_clip(item)
            except Exception as exc:
                logger.exception("Clip finalization failed: %s", exc)
            self._clip_finalize_queue.task_done()

    def _update_pending_clips(self, frame, ts: datetime) -> None:
        """Append raw frame to active clips; send completed clips to background thread."""
        remaining: list[dict] = []
        for clip in self.pending_clips:
            if ts <= clip["end_ts"]:
                clip["frames"].append(frame.copy())
                remaining.append(clip)
            else:
                # Offload VideoWriter encoding to background thread (Fix 8)
                self._clip_finalize_queue.put(clip)
        self.pending_clips = remaining

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def _debug_dump(self, signal: TheftSignal, risk_score: float, state: str) -> None:
        if self.settings.debug_track_id is None or self.settings.debug_track_id != signal.track_id:
            return
        if self.debug_file is None:
            self.settings.debug_dump_path.mkdir(parents=True, exist_ok=True)
            self.debug_file = self.settings.debug_dump_path / f"track_{signal.track_id}.csv"
            if not self.debug_file.exists():
                self.debug_file.write_text(
                    "ts,track_id,signal_type,value,risk_score,state,details\n",
                    encoding="utf-8",
                )
        with self.debug_file.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    signal.ts.isoformat(),
                    signal.track_id,
                    signal.signal_type,
                    signal.value,
                    risk_score,
                    state,
                    signal.details,
                ]
            )

    # ------------------------------------------------------------------
    # Customer / item helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _customer_id(camera_id: str, track_id: int) -> str:
        return f"{camera_id}:{track_id}"

    def _resolve_global_customer_id(self, frame, track, ts: datetime) -> str:
        x1, y1, x2, y2 = [int(v) for v in track.bbox]
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w - 1, x2), min(h - 1, y2)
        crop = frame[y1:y2, x1:x2] if (x2 > x1 and y2 > y1) else None
        emb: list[float] = []
        if crop is not None and crop.size > 0:
            reid = self.reid.embed(crop)
            if reid is not None:
                emb = reid.embedding
        height_px = float(max(1.0, y2 - y1))
        return self.identity_resolver.match_or_create(
            camera_id=self.settings.camera_id,
            local_track_id=track.track_id,
            embedding=emb,
            ts=ts,
            height_px=height_px,
        )

    @staticmethod
    def _basket_to_dict(basket) -> dict:
        return {
            "items_in_hand": sorted(list(basket.items_in_hand)),
            "items_concealed": sorted(list(basket.items_concealed)),
            "items_on_counter": sorted(list(basket.items_on_counter)),
            "items_returned": sorted(list(basket.items_returned)),
            "inferred_total_possessed": sorted(list(basket.inferred_total_possessed())),
            "hand_count": len(basket.items_in_hand),
            "concealed_count": len(basket.items_concealed),
            "counter_count": len(basket.items_on_counter),
            "returned_count": len(basket.items_returned),
        }

    def _allow_risk_decay(self, customer_id: str) -> bool:
        basket = self.item_state_machine.get_basket(customer_id)
        return len(basket.items_concealed) == 0 and not self.item_state_machine.get_mismatch_unresolved(customer_id)

    @staticmethod
    def _get_zone_name(centroid: tuple[float, float], zones: ZoneConfig) -> str:
        for name, polygon in zones.zones.items():
            if polygon and is_point_in_zone(centroid, polygon):
                return name
        return "unknown"

    def _upsert_customer_runtime(self, db, customer_id: str, track, ts: datetime, risk_score: float, zones: ZoneConfig) -> None:
        zone_name = self._get_zone_name(track.centroid, zones)
        basket = self.item_state_machine.get_basket(customer_id)
        crud.upsert_customer(
            db=db,
            global_customer_id=customer_id,
            current_camera_id=self.settings.camera_id,
            current_zone=zone_name,
            entry_time=self.customer_first_seen.get(customer_id, ts),
            last_seen_time=ts,
            risk_score_current=risk_score,
            basket_state=self._basket_to_dict(basket),
            evidence_links=self.customer_evidence_links.get(customer_id, []),
        )

    def _upsert_item_runtime(self, db, customer_id: str, item_id: str, event_type: str, ts: datetime) -> None:
        status_map = {
            "PICK": "IN_HAND",
            "PUT_BACK": "RETURNED",
            "CONCEAL_POCKET": "CONCEALED",
            "CONCEAL_BAG": "CONCEALED",
            "CONCEAL_HOODIE": "CONCEALED",
            "CONCEAL_PANTS": "CONCEALED",
            "CONCEAL_SHIRT": "CONCEALED",
            "LOST_UNCERTAIN": "LOST_UNCERTAIN",
            "ON_COUNTER": "ON_COUNTER",
        }
        status = status_map.get(event_type, "ON_SHELF")
        disappearance_reason = None
        if event_type.startswith("CONCEAL_"):
            disappearance_reason = event_type.removeprefix("CONCEAL_")
        crud.upsert_item(
            db=db,
            global_item_id=item_id,
            item_class="generic_product",
            current_status=status,
            owner_customer_id=customer_id,
            last_seen_camera_id=self.settings.camera_id,
            first_pick_time=ts if event_type == "PICK" else None,
            last_status_change_time=ts,
            confidence=0.6,
            disappearance_reason=disappearance_reason,
        )

    def _next_item_id(self, customer_id: str) -> str:
        idx = self.track_item_counter.get(customer_id, 0) + 1
        self.track_item_counter[customer_id] = idx
        return f"{customer_id}:item:{idx}"

    def _map_signals_to_item_actions(self, customer_id: str, signals: list[TheftSignal], ts: datetime) -> list[tuple[str, str | None]]:
        actions: list[tuple[str, str | None]] = []
        active_items = self.track_active_items.setdefault(customer_id, [])
        basket = self.item_state_machine.get_basket(customer_id)

        # PRIORITY RULE: evaluate put-back before concealment.
        shelf_interactions = [s for s in signals if s.signal_type == "SHELF_INTERACTION"]
        if shelf_interactions and basket.items_in_hand:
            if self.last_pick_ts.get(customer_id) and (ts - self.last_pick_ts[customer_id]).total_seconds() > self.settings.conceal_window_sec:
                item_to_put_back = sorted(list(basket.items_in_hand))[0]
                actions.append(("PUT_BACK", item_to_put_back))
                if item_to_put_back in active_items:
                    active_items.remove(item_to_put_back)
                return actions

        conceal_signal_present = any(s.signal_type == "HAND_TO_POCKET" for s in signals)
        bag_signal_present = any(s.signal_type == "HAND_TO_BAG" for s in signals)

        for signal in signals:
            if signal.signal_type == "SHELF_INTERACTION":
                last_pick = self.last_pick_ts.get(customer_id)
                if last_pick is None or (ts - last_pick).total_seconds() >= self.settings.conceal_window_sec:
                    item_id = self._next_item_id(customer_id)
                    active_items.append(item_id)
                    self.last_pick_ts[customer_id] = ts
                    actions.append(("PICK", item_id))
                    history = self.pick_history.setdefault(customer_id, deque(maxlen=8))
                    history.append(ts)
                    if len(history) >= 2 and (history[-1] - history[-2]).total_seconds() <= 2.5:
                        actions.append(("RAPID_MULTI_PICK", None))
            elif signal.signal_type == "HAND_TO_BAG":
                if active_items:
                    if self.settings.camera_role == "COUNTER":
                        actions.append(("ON_COUNTER", active_items[-1]))
                    else:
                        actions.append(("CONCEAL_BAG", active_items[-1]))
            elif signal.signal_type == "HAND_TO_POCKET":
                if active_items:
                    actions.append(("CONCEAL_POCKET", active_items[-1]))

        # Weak disappearance heuristic -> LOST_UNCERTAIN when in-hand items exist without strong conceal cue.
        if not signals and basket.items_in_hand and not conceal_signal_present and not bag_signal_present:
            maybe_item = sorted(list(basket.items_in_hand))[0]
            if maybe_item not in self.item_state_machine.get_lost_uncertain_items(customer_id):
                actions.append(("LOST_UNCERTAIN", maybe_item))
        return actions

    def _state_event_to_theft_event(self, state_event: StateEvent, track_id: int, risk_score: float) -> TheftEvent:
        return TheftEvent(
            event_id="",
            camera_id=self.settings.camera_id,
            ts_start=state_event.ts_start,
            ts_trigger=state_event.ts_end,
            track_id=track_id,
            event_type=state_event.event_type,
            risk_score_at_trigger=round(risk_score, 2),
            snapshot_path=None,
            short_explanation=state_event.explanation,
            details={
                "customer_id": state_event.customer_id,
                "involved_item_ids": state_event.involved_item_ids,
                "source": "item_state_machine",
            },
        )

    def _clip_policy_status_for_event(self, event_type: str) -> str:
        if event_type in {
            "CONCEAL_POCKET",
            "CONCEAL_BAG",
            "CONCEAL_HOODIE",
            "CONCEAL_PANTS",
            "CONCEAL_SHIRT",
            "COUNTER_MISMATCH",
            "EXIT_ALERT",
        }:
            return "KEEP"
        return "TEMP"

    def _apply_session_close_policy(self, db, customer_id: str, alert: bool, now: datetime) -> None:
        clips = crud.list_clips_for_customer(db=db, customer_id=customer_id, limit=2000)
        critical_events = {"CONCEAL_POCKET", "CONCEAL_BAG", "CONCEAL_HOODIE", "COUNTER_MISMATCH", "EXIT_ALERT"}
        if alert:
            for clip in clips:
                event = crud.get_event_by_event_id(db=db, event_id=clip.event_id)
                event_type = event.event_type if event is not None else ""
                if event_type not in critical_events:
                    continue
                crud.update_clip_retention(
                    db=db,
                    clip_id=clip.clip_id,
                    status="KEEP",
                    retention_until=now + timedelta(days=30),
                )
            return
        for clip in clips:
            if clip.status != "TEMP":
                continue
            crud.update_clip_retention(
                db=db,
                clip_id=clip.clip_id,
                status="DELETE_PENDING",
                retention_until=now + timedelta(hours=24),
            )

    def _select_evidence_clips_for_missing_items(self, db, customer_id: str, missing_item_ids: list[str]) -> list[str]:
        conceal_by_item: dict[str, str] = {}
        pick_by_item: dict[str, str] = {}
        events = crud.list_events(db=db, limit=1000)
        for row in events:
            data = crud.serialize_event(row)
            details = data.get("details", {})
            if details.get("customer_id") != customer_id:
                continue
            item_ids = details.get("involved_item_ids", [])
            if not isinstance(item_ids, list):
                continue
            clip_path = details.get("clip_path")
            if not clip_path:
                continue
            if data.get("event_type", "").startswith("CONCEAL_"):
                for item_id in item_ids:
                    conceal_by_item.setdefault(str(item_id), str(clip_path))
            if data.get("event_type") == "PICK":
                for item_id in item_ids:
                    pick_by_item.setdefault(str(item_id), str(clip_path))

        chosen: list[str] = []
        for item_id in missing_item_ids:
            clip = conceal_by_item.get(item_id) or pick_by_item.get(item_id)
            if clip and clip not in chosen:
                chosen.append(clip)
        return chosen

    def _handle_counter_session(
        self,
        db,
        customer_id: str,
        track_id: int,
        ts: datetime,
        zones: ZoneConfig,
        track,
        out_events: list[TheftEvent],
        force_reconcile: bool = False,
    ) -> None:
        if self.settings.camera_role != "COUNTER":
            return
        counter_polygon = zones.zones.get("counter_zone", []) or zones.zones.get("checkout_zone", [])
        in_counter = bool(counter_polygon and is_point_in_zone(track.centroid, counter_polygon))
        state = self.counter_session_state.setdefault(
            customer_id,
            {
                "checkout_started": False,
                "missing_timer_start_ts": None,
                "was_in_counter": False,
            },
        )
        basket = self.item_state_machine.get_basket(customer_id)
        hand_count = len(basket.items_in_hand)
        hidden_count = len(basket.items_concealed)
        counter_count = len(basket.items_on_counter)
        seen_now = counter_count + hand_count
        expected = counter_count + hand_count + hidden_count
        missing = max(0, expected - seen_now)

        if in_counter and counter_count > 0:
            state["checkout_started"] = True

        mismatch_open = self.item_state_machine.get_mismatch_unresolved(customer_id)
        should_run = in_counter and state["checkout_started"]
        if force_reconcile and state["checkout_started"]:
            should_run = True

        if should_run:
            if missing > 0:
                if state["missing_timer_start_ts"] is None:
                    state["missing_timer_start_ts"] = ts
                elapsed = (ts - state["missing_timer_start_ts"]).total_seconds()
                if elapsed >= self.counter_missing_persist_seconds and not mismatch_open:
                    self.item_state_machine.set_mismatch_unresolved(customer_id, True)
                    self.item_state_machine.apply(customer_id=customer_id, signal_type="COUNTER_MISMATCH", item_id=None, ts=ts)
                    score = self.item_risk.apply_delta(customer_id, 20.0, "COUNTER_MISMATCH", ts, allow_decay=False)
                    missing_item_ids = sorted(list(basket.items_concealed))
                    evidence_clips = self._select_evidence_clips_for_missing_items(
                        db=db,
                        customer_id=customer_id,
                        missing_item_ids=missing_item_ids,
                    )
                    out_events.append(
                        TheftEvent(
                            event_id="",
                            camera_id=self.settings.camera_id,
                            ts_start=ts,
                            ts_trigger=ts,
                            track_id=track_id,
                            event_type="COUNTER_MISMATCH",
                            risk_score_at_trigger=round(score, 2),
                            snapshot_path=None,
                            short_explanation=f"Missing {missing} item(s) at counter.",
                            details={
                                "customer_id": customer_id,
                                "missing_count": missing,
                                "missing_item_ids": missing_item_ids,
                                "evidence_clip_paths": evidence_clips,
                            },
                        )
                    )
            else:
                state["missing_timer_start_ts"] = None
                if mismatch_open:
                    self.item_state_machine.set_mismatch_unresolved(customer_id, False)
                    self.item_state_machine.apply(customer_id=customer_id, signal_type="COUNTER_RECONCILED", item_id=None, ts=ts)
                    score = self.item_risk.apply_delta(customer_id, -10.0, "COUNTER_RECONCILED", ts, allow_decay=False)
                    out_events.append(
                        TheftEvent(
                            event_id="",
                            camera_id=self.settings.camera_id,
                            ts_start=ts,
                            ts_trigger=ts,
                            track_id=track_id,
                            event_type="COUNTER_RECONCILED",
                            risk_score_at_trigger=round(score, 2),
                            snapshot_path=None,
                            short_explanation="Counter fully reconciled.",
                            details={
                                "customer_id": customer_id,
                                "missing_count": 0,
                            },
                        )
                    )
        elif not in_counter:
            state["missing_timer_start_ts"] = None
        state["was_in_counter"] = in_counter

    def _handle_exit_crossing(
        self,
        db,
        customer_id: str,
        track_id: int,
        ts: datetime,
        zones: ZoneConfig,
        track,
        out_events: list[TheftEvent],
    ) -> None:
        if self.settings.camera_role != "ENTRY_EXIT":
            return
        exit_polygon = zones.zones.get("exit_zone", [])
        if not exit_polygon:
            return
        in_exit = is_point_in_zone(track.centroid, exit_polygon)
        prev = self.customer_prev_in_exit_zone.get(customer_id, False)
        self.customer_prev_in_exit_zone[customer_id] = in_exit
        if not in_exit or prev:
            return

        self._handle_counter_session(
            db=db,
            customer_id=customer_id,
            track_id=track_id,
            ts=ts,
            zones=zones,
            track=track,
            out_events=out_events,
            force_reconcile=True,
        )
        state_events = self.item_state_machine.apply(customer_id=customer_id, signal_type="EXIT", item_id=None, ts=ts)
        for ev in state_events:
            if ev.risk_delta != 0:
                self.item_risk.apply_delta(customer_id, ev.risk_delta, ev.event_type, ts, allow_decay=False)
            if ev.event_type == "EXIT_ALERT" and self.item_state_machine.get_lost_uncertain_items(customer_id):
                self.item_risk.apply_delta(customer_id, 15.0, "UNCERTAIN_UPGRADED_ON_EXIT", ts, allow_decay=False)
            event = self._state_event_to_theft_event(
                state_event=ev,
                track_id=track_id,
                risk_score=self.item_risk.decay(customer_id, ts, allow_decay=False),
            )
            out_events.append(event)
            if ev.event_type in {"EXIT_ALERT", "EXIT_CLEARED"}:
                self.customer_alert_state[customer_id] = ev.event_type == "EXIT_ALERT"
                self._apply_session_close_policy(
                    db=db,
                    customer_id=customer_id,
                    alert=ev.event_type == "EXIT_ALERT",
                    now=ts,
                )

    def _emit_event(
        self,
        db,
        event: TheftEvent,
        frame,
        contributing_signals: list[TheftSignal],
    ) -> None:
        event_id = str(uuid.uuid4())
        snapshot_path = self._save_snapshot(frame, event_id) if frame is not None else None
        clip_id, clip_path = self._start_event_clip(event_id, event.track_id, event.ts_trigger, db=db)
        crud.update_clip_status(db=db, clip_id=clip_id, status=self._clip_policy_status_for_event(event.event_type), clip_path=clip_path)
        details = dict(event.details)
        details["clip_id"] = clip_id
        details["clip_path"] = clip_path

        crud.create_event(
            db=db,
            event_id=event_id,
            camera_id=event.camera_id,
            track_id=event.track_id,
            event_type=event.event_type,
            ts_start=event.ts_start,
            ts_trigger=event.ts_trigger,
            risk_score_at_trigger=event.risk_score_at_trigger,
            short_explanation=event.short_explanation,
            snapshot_path=snapshot_path,
            details=details,
        )
        crud.create_signals(db=db, camera_id=event.camera_id, signals=contributing_signals, event_id=event_id)
        send_event_webhook(
            settings=self.settings,
            payload={
                "event_id": event_id,
                "camera_id": event.camera_id,
                "track_id": event.track_id,
                "event_type": event.event_type,
                "risk_score_at_trigger": event.risk_score_at_trigger,
                "ts_trigger": event.ts_trigger.isoformat(),
                "short_explanation": event.short_explanation,
                "snapshot_path": snapshot_path,
                "clip_path": clip_path,
                "details": details,
            },
        )
        details_customer_id = details.get("customer_id")
        if details_customer_id:
            customer_id = str(details_customer_id)
            self.customer_evidence_links.setdefault(customer_id, []).append(clip_id)
        elif event.track_id is not None:
            customer_id = self._customer_id(event.camera_id, event.track_id)
            self.customer_evidence_links.setdefault(customer_id, []).append(clip_id)
        logger.info(
            f"Event emitted: {event.event_type} track={event.track_id} risk={event.risk_score_at_trigger:.2f}"
        )

    # ------------------------------------------------------------------
    # Main pipeline loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        logger.info("Pipeline runner started")
        init_db(self.settings.db_url)

        # Start background DB writer after DB is initialised (Fix 3)
        session_local = get_session_local()
        db_writer = DBWriteWorker(session_local)
        db_writer.start()
        self._db_writer = db_writer

        source = self._build_source()
        sampler = FrameSampler(self.settings.frame_fps)
        detector = YOLODetector(model_name=self.settings.model_name, conf_thres=self.settings.conf_thres)
        tracker = PersonTracker(use_bytetrack=self.settings.use_bytetrack, iou_thres=self.settings.iou_thres)
        pose = PoseEstimator(model_name=self.settings.pose_model_name, conf_thres=self.settings.wrist_zone_conf_thres)
        theft_fsm = TheftRiskFSM(settings=self.settings)
        # Item detection shares the already-loaded YOLO model — zero extra GPU cost
        item_detector = ItemDetector(detector=detector, enabled=self.settings.item_detection_enabled)
        item_tracker = ItemTracker()

        zones: ZoneConfig | None = None
        db = session_local()
        frame_target_ms = (1.0 / self.settings.frame_fps) * 1.5  # lag threshold (Fix 7)

        try:
            for frame, ts in source.frames():
                if self.stop_event.is_set():
                    break
                if not sampler.should_process(ts):
                    continue

                t0 = time.monotonic()  # Fix 7: lag detection

                try:
                    if zones is None:
                        h, w = frame.shape[:2]
                        zones = load_zone_config(
                            path=self.settings.zones_config_path,
                            camera_id=self.settings.camera_id,
                            frame_width=w,
                            frame_height=h,
                        )
                        logger.info(f"Loaded zones: {list(zones.zones.keys())}")
                    zones = self._active_zones(zones)

                    # Fix 4: store RAW frame in rolling buffer (saves ~50% RAM)
                    self.recent_frames.append((ts, frame.copy()))

                    # Single YOLO call → persons + items (detect_all, zero extra cost)
                    person_dets, item_dets_raw = detector.detect_all(frame)
                    detections = person_dets
                    tracks = tracker.track(frame=frame, detections=detections, detector=detector, ts=ts)
                    pose_map = pose.estimate(frame=frame, tracks=tracks, ts=ts)

                    # Item tracking + visual pick confirmation
                    item_detections = item_detector.from_cached(item_dets_raw)
                    active_item_tracks, disappeared_items = item_tracker.update(item_detections, ts)
                    # Map track_id -> items visually confirmed picked up this frame
                    visual_picks: dict[int, list] = confirm_pick_from_disappeared(
                        disappeared_items=disappeared_items,
                        person_tracks=tracks,
                        pose_map=pose_map,
                        wrist_conf_thres=self.settings.wrist_zone_conf_thres,
                    )

                    all_signals: list[TheftSignal] = []
                    risk_points = []
                    events: list[TheftEvent] = []
                    overlay_metrics: dict[int, dict] = {}

                    # Data collected under the lock for DB writes queued after (Fix 6)
                    items_to_upsert: list[tuple[str, str, str, datetime]] = []
                    customer_upsert_payloads: list[dict] = []

                    for track in tracks:
                        pose_for_track = pose_map.get(
                            track.track_id,
                            PoseKeypoints(
                                keypoints=[],
                                left_wrist=None,
                                right_wrist=None,
                                hip_center=None,
                                available=False,
                                ts=ts,
                            ),
                        )
                        signals, _legacy_event, risk_point = theft_fsm.update_track(
                            camera_id=self.settings.camera_id,
                            track=track,
                            pose=pose_for_track,
                            zones=zones.zones,
                            ts=ts,
                            visually_picked_items=visual_picks.get(track.track_id),
                        )
                        all_signals.extend(signals)
                        risk_points.append(risk_point)
                        customer_id = self._resolve_global_customer_id(frame=frame, track=track, ts=ts)
                        self.customer_first_seen.setdefault(customer_id, ts)

                        # Fix 6: narrow lock scope — only state machine ops inside lock
                        with self.state_lock:
                            actions = self._map_signals_to_item_actions(customer_id=customer_id, signals=signals, ts=ts)
                            for action_type, item_id in actions:
                                state_events = self.item_state_machine.apply(
                                    customer_id=customer_id,
                                    signal_type=action_type,
                                    item_id=item_id,
                                    ts=ts,
                                )
                                delta = state_events[-1].risk_delta if state_events else 0.0
                                if delta != 0.0:
                                    self.item_risk.apply_delta(
                                        customer_id,
                                        delta,
                                        action_type,
                                        ts,
                                        allow_decay=self._allow_risk_decay(customer_id),
                                    )
                                else:
                                    self.item_risk.decay(
                                        customer_id,
                                        ts,
                                        allow_decay=self._allow_risk_decay(customer_id),
                                    )

                                for state_event in state_events:
                                    events.append(
                                        self._state_event_to_theft_event(
                                            state_event=state_event,
                                            track_id=track.track_id,
                                            risk_score=self.item_risk.decay(
                                                customer_id,
                                                ts,
                                                allow_decay=self._allow_risk_decay(customer_id),
                                            ),
                                        )
                                    )
                                    # Collect item upserts — executed outside lock via writer
                                    for involved_item_id in state_event.involved_item_ids:
                                        items_to_upsert.append((customer_id, involved_item_id, state_event.event_type, ts))

                            if self.settings.camera_role == "COUNTER":
                                self._handle_counter_session(
                                    db=db,
                                    customer_id=customer_id,
                                    track_id=track.track_id,
                                    ts=ts,
                                    zones=zones,
                                    track=track,
                                    out_events=events,
                                )
                            self._handle_exit_crossing(
                                db=db,
                                customer_id=customer_id,
                                track_id=track.track_id,
                                ts=ts,
                                zones=zones,
                                track=track,
                                out_events=events,
                            )

                            # Collect customer upsert data under the lock
                            risk_score = self.item_risk.decay(
                                customer_id, ts, allow_decay=self._allow_risk_decay(customer_id)
                            )
                            basket = self.item_state_machine.get_basket(customer_id)
                            basket_snapshot = self._basket_to_dict(basket)
                            zone_name = self._get_zone_name(track.centroid, zones)
                            evidence_links = list(self.customer_evidence_links.get(customer_id, []))

                            hand_count = len(basket.items_in_hand)
                            hidden_count = len(basket.items_concealed)
                            counter_count = len(basket.items_on_counter)
                            seen_now = counter_count + hand_count
                            expected = counter_count + hand_count + hidden_count
                            missing = max(0, expected - seen_now)
                            counter_polygon = zones.zones.get("counter_zone", []) or zones.zones.get("checkout_zone", [])
                            in_counter = bool(counter_polygon and is_point_in_zone(track.centroid, counter_polygon))
                            overlay_metrics[track.track_id] = {
                                "global_customer_id": customer_id,
                                "hand_count": hand_count,
                                "hidden_count": hidden_count,
                                "counter_count": counter_count,
                                "seen_now": seen_now,
                                "expected": expected,
                                "missing": missing,
                                "is_counter_mode": in_counter,
                                "mismatch_unresolved": self.item_state_machine.get_mismatch_unresolved(customer_id),
                            }

                        # Lock released — queue customer upsert via writer (Fix 6)
                        customer_upsert_payloads.append({
                            "global_customer_id": customer_id,
                            "current_camera_id": self.settings.camera_id,
                            "current_zone": zone_name,
                            "entry_time": self.customer_first_seen.get(customer_id, ts),
                            "last_seen_time": ts,
                            "risk_score_current": risk_score,
                            "basket_state": basket_snapshot,
                            "evidence_links": evidence_links,
                        })

                        for signal in signals:
                            self._debug_dump(signal, risk_point.risk_score, risk_point.state)

                    # Queue all item upserts via writer (Fix 6 — outside lock)
                    for cid, item_id, event_type, item_ts in items_to_upsert:
                        self._upsert_item_runtime_queued(cid, item_id, event_type, item_ts)

                    # Queue all customer upserts via writer (Fix 6 — outside lock)
                    for payload in customer_upsert_payloads:
                        db_writer.put("customer_upsert", payload)

                    # Annotate once for stream display
                    risk_scores = {point.track_id: point.risk_score for point in risk_points}
                    states = {point.track_id: point.state for point in risk_points}
                    annotated = annotate_frame(
                        frame=frame,
                        tracks=tracks,
                        zones=zones.zones,
                        risk_scores=risk_scores,
                        track_states=states,
                        pose_map=pose_map,
                        overlay_metrics=overlay_metrics,
                        event_labels=[event.event_type for event in events],
                    )
                    # Fix 5: JPEG encode with rate-limiting
                    self._set_latest_frame(annotated)
                    # Fix 8: pass raw frame to pending clips (already stored in recent_frames)
                    self._update_pending_clips(frame, ts)

                    # Queue signals and track_points via writer (Fix 3 — batch, non-blocking)
                    for signal in all_signals:
                        db_writer.put("signal", {
                            "camera_id": self.settings.camera_id,
                            "track_id": signal.track_id,
                            "signal_type": signal.signal_type,
                            "ts": signal.ts,
                            "value": signal.value,
                            "details": signal.details,
                        })
                    for point in risk_points:
                        db_writer.put("track_point", {
                            "camera_id": point.camera_id,
                            "track_id": point.track_id,
                            "ts": point.ts,
                            "risk_score": point.risk_score,
                            "state": point.state,
                            "centroid_x": point.centroid_x,
                            "centroid_y": point.centroid_y,
                            "velocity": point.velocity,
                            "details": point.details,
                        })

                    if events:
                        for event in events:
                            event_signals = [sig for sig in all_signals if sig.track_id == event.track_id]
                            self._emit_event(db=db, event=event, frame=annotated, contributing_signals=event_signals)

                    theft_fsm.garbage_collect(ts, max_age_seconds=10)

                    # Fix 7: lag detection + frame skip
                    elapsed = time.monotonic() - t0
                    if self.settings.drop_frames_when_lagging and elapsed > frame_target_ms:
                        logger.debug(
                            "Frame took %.0fms (budget %.0fms) — skipping next",
                            elapsed * 1000,
                            frame_target_ms * 1000,
                        )
                        sampler.force_skip()

                except Exception as exc:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    if is_sqlite_corruption_error(exc):
                        logger.error("Detected SQLite corruption; attempting automatic database recovery")
                        try:
                            db.close()
                        except Exception:
                            pass
                        try:
                            backup_path = recover_sqlite_database(self.settings.db_url)
                            session_local = get_session_local()
                            db = session_local()
                            backup_note = str(backup_path) if backup_path else "none"
                            logger.warning(
                                f"SQLite database recovered. Fresh DB initialized. Backup file: {backup_note}"
                            )
                            continue
                        except Exception as recovery_exc:
                            logger.exception(f"Automatic database recovery failed: {recovery_exc}")
                    logger.exception(f"Pipeline iteration failed: {exc}")
        finally:
            for clip in list(self.pending_clips):
                self._finalize_clip(clip)
            self.pending_clips = []
            db.close()
            db_writer.stop()
            self._db_writer = None
            logger.info("Pipeline runner stopped")

    def _upsert_item_runtime_queued(self, customer_id: str, item_id: str, event_type: str, ts: datetime) -> None:
        """Queue an item upsert through the background DB writer."""
        status_map = {
            "PICK": "IN_HAND",
            "PUT_BACK": "RETURNED",
            "CONCEAL_POCKET": "CONCEALED",
            "CONCEAL_BAG": "CONCEALED",
            "CONCEAL_HOODIE": "CONCEALED",
            "CONCEAL_PANTS": "CONCEALED",
            "CONCEAL_SHIRT": "CONCEALED",
            "LOST_UNCERTAIN": "LOST_UNCERTAIN",
            "ON_COUNTER": "ON_COUNTER",
        }
        status = status_map.get(event_type, "ON_SHELF")
        disappearance_reason = event_type.removeprefix("CONCEAL_") if event_type.startswith("CONCEAL_") else None
        if self._db_writer:
            self._db_writer.put("item_upsert", {
                "global_item_id": item_id,
                "item_class": "generic_product",
                "current_status": status,
                "owner_customer_id": customer_id,
                "last_seen_camera_id": self.settings.camera_id,
                "first_pick_time": ts if event_type == "PICK" else None,
                "last_status_change_time": ts,
                "confidence": 0.6,
                "disappearance_reason": disappearance_reason,
            })


_runner_singleton: PipelineRunner | None = None


def get_pipeline_runner(settings: Settings) -> PipelineRunner:
    global _runner_singleton
    if _runner_singleton is None:
        _runner_singleton = PipelineRunner(settings)
    return _runner_singleton
