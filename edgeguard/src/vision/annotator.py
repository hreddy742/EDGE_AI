import cv2
import numpy as np

from src.rules.zones import Point
from src.vision.pose import PoseKeypoints
from src.vision.tracker import PersonTrack


def _zone_color(name: str) -> tuple[int, int, int]:
    if "exit" in name:
        return (0, 165, 255)
    if "shelf" in name:
        return (255, 255, 0)
    if "bag" in name:
        return (255, 0, 255)
    if "scanner" in name:
        return (255, 0, 0)
    return (120, 220, 120)


def annotate_frame(
    frame: np.ndarray,
    tracks: list[PersonTrack],
    zones: dict[str, list[Point]],
    risk_scores: dict[int, float],
    track_states: dict[int, str],
    pose_map: dict[int, PoseKeypoints],
    overlay_metrics: dict[int, dict] | None = None,
    event_labels: list[str] | None = None,
) -> np.ndarray:
    annotated = frame.copy()

    for name, polygon in zones.items():
        if not polygon:
            continue
        color = _zone_color(name)
        pts = np.array(polygon, dtype=np.int32)
        cv2.polylines(annotated, [pts], isClosed=True, color=color, thickness=2)
        cv2.putText(
            annotated,
            name,
            polygon[0],
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )

    overlay_metrics = overlay_metrics or {}

    for track in tracks:
        x1, y1, x2, y2 = [int(v) for v in track.bbox]
        risk = risk_scores.get(track.track_id, 0.0)
        state = track_states.get(track.track_id, "BROWSING")
        if risk >= 50.0:
            color = (0, 0, 255)
            status = "RED"
        elif risk >= 25.0:
            color = (0, 255, 255)
            status = "YELLOW"
        else:
            color = (0, 255, 0)
            status = "GREEN"
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)

        badge_text = str(track.track_id)
        font_scale = 0.7
        thickness = 2

        (tw, th), _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        pad = 4
        by1 = max(0, y1 - th - 2 * pad - 6)
        by2 = by1 + th + 2 * pad
        bx1 = max(0, x1)
        bx2 = min(annotated.shape[1] - 1, bx1 + tw + 2 * pad)
        cv2.rectangle(annotated, (bx1, by1), (bx2, by2), (20, 20, 20), -1)
        cv2.rectangle(annotated, (bx1, by1), (bx2, by2), color, 1)
        cv2.putText(
            annotated,
            badge_text,
            (bx1 + pad, by2 - pad - 1),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            thickness,
        )

        pose = pose_map.get(track.track_id)
        if pose is None:
            continue
        for pt in [pose.left_wrist, pose.right_wrist]:
            if pt is not None:
                cv2.circle(annotated, (int(pt[0]), int(pt[1])), 4, (255, 255, 255), -1)
        if pose.hip_center is not None:
            cv2.circle(annotated, (int(pose.hip_center[0]), int(pose.hip_center[1])), 5, (0, 255, 255), -1)

    if event_labels:
        text = ", ".join(event_labels[-4:])
        cv2.putText(
            annotated,
            f"Events: {text}",
            (8, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 165, 255),
            2,
        )

    return annotated
