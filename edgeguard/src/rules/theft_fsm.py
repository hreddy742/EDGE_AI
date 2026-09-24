from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import numpy as np

from src.core.config import Settings
from src.rules.association import detect_conceal_type_from_pose
from src.rules.zones import is_point_in_zone
from src.vision.pose import PoseKeypoints
from src.vision.tracker import PersonTrack

State = Literal[
    "BROWSING",
    "NEAR_SHELF",
    "SHELF_INTERACTION",
    "PICK_SUSPECTED",
    "POSSIBLE_CONCEALMENT",
    "CONFIRMED_CONCEALMENT",
    "HIGH_RISK_EXIT",
    "CLEARED",
]


@dataclass
class TheftSignal:
    signal_type: str
    track_id: int
    ts: datetime
    value: float
    details: dict


@dataclass
class TheftEvent:
    event_id: str
    camera_id: str
    ts_start: datetime
    ts_trigger: datetime
    track_id: int
    event_type: str
    risk_score_at_trigger: float
    snapshot_path: str | None
    short_explanation: str
    details: dict = field(default_factory=dict)


@dataclass
class TrackRiskPoint:
    camera_id: str
    track_id: int
    ts: datetime
    risk_score: float
    state: str
    centroid_x: float
    centroid_y: float
    velocity: float
    details: dict = field(default_factory=dict)


@dataclass
class TrackContext:
    state: State = "BROWSING"
    risk_score: float = 0.0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    last_event_ts: datetime | None = None
    last_centroid: tuple[float, float] | None = None
    hand_in_shelf_frames: int = 0
    repeated_shelf_interactions: int = 0
    last_shelf_interaction_ts: datetime | None = None
    last_pick_ts: datetime | None = None
    last_conceal_ts: datetime | None = None
    last_scan_ts: datetime | None = None
    last_bag_ts: datetime | None = None
    active_signals: list[str] = field(default_factory=list)
    last_pick_visual: bool = False   # True when item disappearance confirmed pick
    last_decay_ts: datetime | None = None


class TheftRiskFSM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tracks: dict[int, TrackContext] = {}
        self.weights = {
            "SHELF_INTERACTION":        2.0,
            "VISUAL_PICK_CONFIRMED":    4.0,   # stronger — visual evidence
            "HAND_TO_POCKET":           4.0,
            "HAND_TO_BAG":              3.0,
            "HAND_TO_PANTS":            4.5,   # pants concealment is very intentional
            "HAND_TO_SHIRT":            3.5,
            "HAND_TO_HOODIE":           4.0,
            "EXIT_AFTER_CONCEALMENT":   6.0,
            "REPEATED_SHELF_INTERACTION": 2.0,
            "NONSCAN_BAGGING":          5.0,
            "SPEED_SPIKE":              1.5,
        }

    @staticmethod
    def _wrist_points(pose: PoseKeypoints) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        if pose.left_wrist is not None:
            out.append(pose.left_wrist)
        if pose.right_wrist is not None:
            out.append(pose.right_wrist)
        return out

    @staticmethod
    def _explain(signals: list[TheftSignal]) -> str:
        mapping = {
            "SHELF_INTERACTION":        "hand interacted with shelf zone",
            "VISUAL_PICK_CONFIRMED":    "item visually confirmed picked up",
            "HAND_TO_POCKET":           "hand moved to pocket/hip region",
            "HAND_TO_BAG":              "hand moved to bag",
            "HAND_TO_PANTS":            "hand moved to trouser pocket area",
            "HAND_TO_SHIRT":            "hand moved to shirt/chest area",
            "HAND_TO_HOODIE":           "hand moved above shoulder (hoodie)",
            "EXIT_AFTER_CONCEALMENT":   "person exited after concealment pattern",
            "NONSCAN_BAGGING":          "bagging detected without scanner interaction",
            "SPEED_SPIKE":              "rapid movement near exit",
        }
        parts: list[str] = []
        for signal in signals[-3:]:
            text = mapping.get(signal.signal_type)
            if text and text not in parts:
                parts.append(text)
        return "; ".join(parts) if parts else "multiple suspicious temporal signals"

    def _risk_decay(self, context: TrackContext, now: datetime) -> None:
        # Use time-based decay so behavior is stable across varying input FPS.
        if context.last_decay_ts is None:
            context.last_decay_ts = now
            return
        dt = max(0.0, (now - context.last_decay_ts).total_seconds())
        decay_per_sec = 1.8  # equivalent to 0.15/frame at 12 FPS baseline
        context.risk_score = max(0.0, context.risk_score - (decay_per_sec * dt))
        context.last_decay_ts = now

    def _emit(
        self,
        signal_type: str,
        track_id: int,
        ts: datetime,
        value: float,
        details: dict,
        context: TrackContext,
        out: list[TheftSignal],
    ) -> None:
        context.risk_score += self.weights.get(signal_type, 0.0) * value
        context.active_signals.append(signal_type)
        out.append(TheftSignal(signal_type=signal_type, track_id=track_id, ts=ts, value=value, details=details))

    def update_track(
        self,
        camera_id: str,
        track: PersonTrack,
        pose: PoseKeypoints,
        zones: dict[str, list[tuple[int, int]]],
        ts: datetime,
        visually_picked_items: list | None = None,  # list[ItemTrack] from item tracker
    ) -> tuple[list[TheftSignal], TheftEvent | None, TrackRiskPoint]:
        ctx = self.tracks.get(track.track_id)
        if ctx is None:
            ctx = TrackContext(first_seen=ts, last_seen=ts, last_decay_ts=ts)
            self.tracks[track.track_id] = ctx
        ctx.last_seen = ts
        self._risk_decay(ctx, ts)
        signals: list[TheftSignal] = []

        shelf_polygon   = zones.get("shelf_zone", [])
        exit_polygon    = zones.get("exit_zone", [])
        bag_polygon     = zones.get("bagging_zone", [])
        scanner_polygon = zones.get("scanner_zone", [])

        # Proximity state update (no signal — just zone awareness)
        near_shelf = is_point_in_zone(track.centroid, shelf_polygon)
        if near_shelf:
            ctx.state = "NEAR_SHELF"

        wrists = self._wrist_points(pose)

        # ------------------------------------------------------------------
        # Pick detection — visual evidence first, pose heuristic as fallback
        # ------------------------------------------------------------------
        pick_fired = False

        # 1. Visually confirmed: an item track disappeared near this person's wrist
        if visually_picked_items:
            ctx.last_pick_ts = ts
            ctx.last_pick_visual = True
            ctx.state = "PICK_SUSPECTED"
            for item in visually_picked_items:
                self._emit(
                    "VISUAL_PICK_CONFIRMED",
                    track.track_id,
                    ts,
                    1.0,
                    {"item_id": item.global_item_id, "item_cls": item.cls},
                    ctx,
                    signals,
                )
            pick_fired = True

        # 2. Pose heuristic: wrist sustained inside shelf zone
        wrist_in_shelf = any(is_point_in_zone(w, shelf_polygon) for w in wrists) if wrists else False
        if wrist_in_shelf:
            ctx.hand_in_shelf_frames += 1
        else:
            if ctx.hand_in_shelf_frames >= self.settings.n_frames_hand_in_shelf:
                ctx.last_pick_ts = ts
                ctx.last_pick_visual = False
                ctx.state = "PICK_SUSPECTED"
            ctx.hand_in_shelf_frames = 0

        if not pick_fired and ctx.hand_in_shelf_frames >= self.settings.n_frames_hand_in_shelf:
            ctx.state = "SHELF_INTERACTION"
            ctx.last_shelf_interaction_ts = ts
            ctx.repeated_shelf_interactions += 1
            self._emit(
                "SHELF_INTERACTION",
                track.track_id,
                ts,
                1.0,
                {"frames": ctx.hand_in_shelf_frames},
                ctx,
                signals,
            )
            if ctx.repeated_shelf_interactions > 1:
                self._emit("REPEATED_SHELF_INTERACTION", track.track_id, ts, 1.0, {}, ctx, signals)

        # ------------------------------------------------------------------
        # Concealment detection with skeleton-based type classification
        # ------------------------------------------------------------------
        recent_pick = (
            ctx.last_pick_ts is not None
            and (ts - ctx.last_pick_ts).total_seconds() <= self.settings.conceal_window_sec
        )

        # Build keypoints array for geometry analysis
        kpts_array: np.ndarray | None = None
        if pose.keypoints:
            try:
                kpts_array = np.array(pose.keypoints, dtype=float)
            except Exception:
                kpts_array = None

        # Wrist-to-hip distance — covers cases where item detection missed pick
        if pose.hand_to_hip_distance is not None and pose.hand_to_hip_distance <= self.settings.hand_to_hip_distance_px:
            if recent_pick:
                ctx.state = "POSSIBLE_CONCEALMENT"
                ctx.last_conceal_ts = ts

                # Classify type from skeleton geometry
                conceal_type: str | None = None
                if kpts_array is not None:
                    conceal_type = detect_conceal_type_from_pose(
                        kpts=kpts_array,
                        bag_zone=bag_polygon if bag_polygon else None,
                        active_wrists=wrists,
                    )

                if conceal_type == "BAG":
                    signal_type = "HAND_TO_BAG"
                elif conceal_type in {"PANTS", "SHIRT", "HOODIE"}:
                    signal_type = f"HAND_TO_{conceal_type}"
                else:
                    signal_type = "HAND_TO_POCKET"

                self._emit(
                    signal_type,
                    track.track_id,
                    ts,
                    1.0,
                    {
                        "distance_px": round(pose.hand_to_hip_distance, 2),
                        "conceal_type": conceal_type,
                        "visual_pick": ctx.last_pick_visual,
                    },
                    ctx,
                    signals,
                )

        # Wrist in bag zone independently (catches bag concealment where person
        # doesn't bring hand near hip)
        wrist_in_bag = any(is_point_in_zone(w, bag_polygon) for w in wrists) if wrists else False
        if wrist_in_bag and recent_pick:
            ctx.last_bag_ts = ts
            ctx.last_conceal_ts = ts
            ctx.state = "POSSIBLE_CONCEALMENT"
            self._emit(
                "HAND_TO_BAG",
                track.track_id,
                ts,
                1.0,
                {"zone": "bagging_zone", "visual_pick": ctx.last_pick_visual},
                ctx,
                signals,
            )

        # ------------------------------------------------------------------
        # Scanner / self-checkout
        # ------------------------------------------------------------------
        wrist_in_scanner = any(is_point_in_zone(w, scanner_polygon) for w in wrists) if wrists else False
        if wrist_in_scanner:
            ctx.last_scan_ts = ts

        if self.settings.mode == "self_checkout":
            if ctx.last_bag_ts is not None:
                scan_ok = (
                    ctx.last_scan_ts is not None
                    and (ctx.last_bag_ts - ctx.last_scan_ts).total_seconds() <= self.settings.conceal_window_sec
                )
                if not scan_ok:
                    self._emit(
                        "NONSCAN_BAGGING",
                        track.track_id,
                        ts,
                        1.0,
                        {"bagging_without_scan": True},
                        ctx,
                        signals,
                    )

        # ------------------------------------------------------------------
        # Exit crossing
        # ------------------------------------------------------------------
        prev_centroid = ctx.last_centroid
        exit_cross = is_point_in_zone(track.centroid, exit_polygon) and (
            prev_centroid is None or not is_point_in_zone(prev_centroid, exit_polygon)
        )
        if exit_cross and ctx.last_conceal_ts is not None:
            ctx.state = "HIGH_RISK_EXIT"
            self._emit(
                "EXIT_AFTER_CONCEALMENT",
                track.track_id,
                ts,
                1.0,
                {"zone": "exit_zone", "visual_pick": ctx.last_pick_visual},
                ctx,
                signals,
            )

        if track.velocity > 140.0 and is_point_in_zone(track.centroid, exit_polygon):
            self._emit("SPEED_SPIKE", track.track_id, ts, 1.0, {"velocity": round(track.velocity, 2)}, ctx, signals)

        ctx.last_centroid = track.centroid

        # ------------------------------------------------------------------
        # Event emission
        # ------------------------------------------------------------------
        event: TheftEvent | None = None
        cooldown_ok = (
            ctx.last_event_ts is None
            or (ts - ctx.last_event_ts).total_seconds() >= self.settings.event_cooldown_seconds
        )
        if ctx.risk_score >= self.settings.risk_threshold and cooldown_ok:
            if self.settings.mode == "self_checkout":
                event_type = "SELF_CHECKOUT_NONSCAN"
            elif ctx.state == "HIGH_RISK_EXIT":
                event_type = "HIGH_RISK_EXIT"
            else:
                event_type = "POSSIBLE_CONCEALMENT"
            event = TheftEvent(
                event_id="",
                camera_id=camera_id,
                ts_start=ctx.first_seen or ts,
                ts_trigger=ts,
                track_id=track.track_id,
                event_type=event_type,
                risk_score_at_trigger=round(ctx.risk_score, 2),
                snapshot_path=None,
                short_explanation=self._explain(signals),
                details={
                    "state": ctx.state,
                    "signals": [s.signal_type for s in signals[-5:]],
                    "visual_pick_confirmed": ctx.last_pick_visual,
                },
            )
            ctx.last_event_ts = ts

        point = TrackRiskPoint(
            camera_id=camera_id,
            track_id=track.track_id,
            ts=ts,
            risk_score=round(ctx.risk_score, 3),
            state=ctx.state,
            centroid_x=round(track.centroid[0], 2),
            centroid_y=round(track.centroid[1], 2),
            velocity=round(track.velocity, 3),
            details={
                "pose_available": pose.available,
                "active_signals": ctx.active_signals[-5:],
                "visual_pick": ctx.last_pick_visual,
            },
        )
        return signals, event, point

    def garbage_collect(self, now: datetime, max_age_seconds: int = 10) -> None:
        stale = [
            tid for tid, ctx in self.tracks.items()
            if ctx.last_seen is not None
            and (now - ctx.last_seen).total_seconds() > max_age_seconds
        ]
        for tid in stale:
            self.tracks.pop(tid, None)
