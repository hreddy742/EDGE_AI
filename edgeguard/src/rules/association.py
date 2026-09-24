"""Hand-item association and concealment-type classification.

Key additions over the original stub:
  confirm_pick_from_disappeared()  — visual pick confirmation when a tracked
                                     item disappears near a person's wrist.
  detect_conceal_type_from_pose()  — classify PANTS / SHIRT / HOODIE / POCKET
                                     / BAG from COCO skeleton geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

import numpy as np

from src.rules.zones import is_point_in_zone


@dataclass
class HandItemAssociation:
    customer_id: str
    item_id: str
    distance_px: float
    stable_frames: int = 0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def wrist_item_distance(
    wrists: list[tuple[float, float]],
    item_bbox: tuple[float, float, float, float],
) -> float:
    center = bbox_center(item_bbox)
    if not wrists:
        return 1e9
    return min(sqrt((w[0] - center[0]) ** 2 + (w[1] - center[1]) ** 2) for w in wrists)


def associate_hand_item(
    customer_pose: dict,
    item_bbox: tuple[float, float, float, float],
) -> float:
    wrists: list[tuple[float, float]] = []
    left = customer_pose.get("left_wrist")
    right = customer_pose.get("right_wrist")
    if isinstance(left, tuple):
        wrists.append(left)
    if isinstance(right, tuple):
        wrists.append(right)
    return wrist_item_distance(wrists=wrists, item_bbox=item_bbox)


# ---------------------------------------------------------------------------
# Visual pick confirmation from disappeared item tracks
# ---------------------------------------------------------------------------

_PICK_WRIST_RADIUS_PX = 120  # max wrist-to-item-center distance to confirm pick


def confirm_pick_from_disappeared(
    disappeared_items: list,       # list[ItemTrack]
    person_tracks: list,           # list[PersonTrack]
    pose_map: dict,                # track_id -> PoseKeypoints
    wrist_conf_thres: float = 0.25,
    pick_radius_px: float = _PICK_WRIST_RADIUS_PX,
) -> dict[int, list]:
    """Map track_id -> list[ItemTrack] that each person visually picked up.

    For every item that just disappeared (past MAX_MISSING_FRAMES), finds the
    person whose wrist was closest and within pick_radius_px.  Returns a dict
    keyed by person track_id.
    """
    picks: dict[int, list] = {}
    if not disappeared_items or not person_tracks:
        return picks

    for item in disappeared_items:
        best_track_id: int | None = None
        best_dist = pick_radius_px

        for track in person_tracks:
            pose = pose_map.get(track.track_id)
            candidate_wrists: list[tuple[float, float]] = []

            if pose is not None:
                if pose.left_wrist is not None and pose.left_wrist_conf >= wrist_conf_thres:
                    candidate_wrists.append(pose.left_wrist)
                if pose.right_wrist is not None and pose.right_wrist_conf >= wrist_conf_thres:
                    candidate_wrists.append(pose.right_wrist)

            if not candidate_wrists:
                candidate_wrists = [track.centroid]

            dist = wrist_item_distance(candidate_wrists, item.bbox)
            if dist < best_dist:
                best_dist = dist
                best_track_id = track.track_id

        if best_track_id is not None:
            picks.setdefault(best_track_id, []).append(item)

    return picks


# ---------------------------------------------------------------------------
# Concealment type from COCO skeleton keypoints
# ---------------------------------------------------------------------------
# COCO keypoint indices
_KP_LEFT_SHOULDER  = 5
_KP_RIGHT_SHOULDER = 6
_KP_LEFT_WRIST     = 9
_KP_RIGHT_WRIST    = 10
_KP_LEFT_HIP       = 11
_KP_RIGHT_HIP      = 12


def _kp(kpts: np.ndarray, idx: int, min_conf: float = 0.3) -> tuple[float, float] | None:
    if idx >= len(kpts):
        return None
    x, y, c = float(kpts[idx][0]), float(kpts[idx][1]), float(kpts[idx][2])
    return (x, y) if c >= min_conf else None


def detect_conceal_type_from_pose(
    kpts: np.ndarray,
    bag_zone: list[tuple[int, int]] | None,
    active_wrists: list[tuple[float, float]],
) -> str | None:
    """Classify concealment type from COCO skeleton geometry.

    Priority: BAG (zone) → PANTS (wrist below hip) → HOODIE (above shoulder)
              → SHIRT (upper torso) → POCKET (hip level, default).

    All Y values are in image coordinates (Y increases downward).
    """
    if kpts is None or len(kpts) < 13 or not active_wrists:
        return None

    # 1. BAG — wrist entered the designated bagging zone polygon
    if bag_zone:
        for w in active_wrists:
            if is_point_in_zone(w, bag_zone):
                return "BAG"

    left_shoulder  = _kp(kpts, _KP_LEFT_SHOULDER)
    right_shoulder = _kp(kpts, _KP_RIGHT_SHOULDER)
    left_hip       = _kp(kpts, _KP_LEFT_HIP)
    right_hip      = _kp(kpts, _KP_RIGHT_HIP)

    shoulder_ys = [p[1] for p in [left_shoulder, right_shoulder] if p]
    hip_ys      = [p[1] for p in [left_hip, right_hip] if p]
    shoulder_y  = sum(shoulder_ys) / len(shoulder_ys) if shoulder_ys else None
    hip_y       = sum(hip_ys) / len(hip_ys) if hip_ys else None

    # Use the wrist that is lowest (most likely concealing hand)
    wrist_y = max(w[1] for w in active_wrists)

    if hip_y is None:
        return "POCKET"  # cannot classify without hip reference

    # 2. PANTS — wrist below hip (trouser pocket / front of trousers)
    if wrist_y > hip_y + 15:
        return "PANTS"

    if shoulder_y is not None:
        mid_y = (shoulder_y + hip_y) / 2.0

        # 3. HOODIE — wrist above shoulder (tucking into hood/collar)
        if wrist_y < shoulder_y - 20:
            return "HOODIE"

        # 4. SHIRT — wrist in upper torso zone (chest/shirt pocket)
        if wrist_y < mid_y:
            return "SHIRT"

    # 5. POCKET — wrist at hip level (trouser side pocket)
    return "POCKET"


# ---------------------------------------------------------------------------
# Legacy helpers (kept for backward compatibility)
# ---------------------------------------------------------------------------

def is_pick_confirmed(context: dict, thresholds) -> bool:
    shelf_frames = int(context.get("shelf_wrist_frames", 0))
    away_frames  = int(context.get("away_with_item_frames", 0))
    return (
        shelf_frames >= int(getattr(thresholds, "n_pick_wrist_shelf_frames", 5))
        and away_frames >= int(getattr(thresholds, "m_pick_away_frames", 6))
    )


def is_putback_confirmed(context: dict, thresholds) -> bool:
    static_frames = int(context.get("item_static_frames", 0))
    wrist_near = bool(context.get("wrist_near_item", False))
    return (
        static_frames >= int(getattr(thresholds, "s_putback_static_frames", 10))
        and wrist_near
    )


def detect_conceal_type(context: dict, thresholds) -> str | None:
    _ = thresholds
    overlaps: dict = context.get("conceal_overlap", {})
    if overlaps.get("bag", 0.0) > 0.5:
        return "BAG"
    if overlaps.get("pocket", 0.0) > 0.5:
        return "POCKET"
    if overlaps.get("hoodie", 0.0) > 0.5:
        return "HOODIE"
    if overlaps.get("shirt", 0.0) > 0.5:
        return "SHIRT"
    return None
