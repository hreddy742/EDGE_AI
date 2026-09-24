import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import get_settings
from src.core.logger import configure_logging, logger
from src.rules.theft_fsm import TheftRiskFSM
from src.rules.zones import load_zone_config
from src.vision.annotator import annotate_frame
from src.vision.detector import YOLODetector
from src.vision.pose import PoseEstimator, PoseKeypoints
from src.vision.tracker import PersonTracker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EdgeGuard theft-risk pipeline on a local video.")
    parser.add_argument("--video", required=True, help="Input video path")
    parser.add_argument("--zones", default="config/zones.sample.json", help="Zones JSON path")
    parser.add_argument("--output", default="data/debug/annotated_output.mp4", help="Annotated output video")
    parser.add_argument("--max-frames", type=int, default=0, help="Optional frame cap (0 = no cap)")
    return parser.parse_args()


def main() -> int:
    configure_logging()
    args = parse_args()
    settings = get_settings()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        logger.error(f"Video not found: {video_path}")
        return 1

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.error(f"Cannot open video: {video_path}")
        return 1

    ok, frame = cap.read()
    if not ok or frame is None:
        logger.error("Failed to read first frame.")
        return 1
    h, w = frame.shape[:2]

    zones = load_zone_config(Path(args.zones).resolve(), camera_id=settings.camera_id, frame_width=w, frame_height=h)

    detector = YOLODetector(model_name=settings.model_name, conf_thres=settings.conf_thres)
    tracker = PersonTracker(use_bytetrack=settings.use_bytetrack, iou_thres=settings.iou_thres)
    pose = PoseEstimator(model_name=settings.pose_model_name, conf_thres=settings.wrist_zone_conf_thres)
    fsm = TheftRiskFSM(settings=settings)

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), max(1, settings.frame_fps), (w, h))

    events: list[dict] = []
    frame_idx = 0
    while ok and frame is not None:
        ts = datetime.now(timezone.utc)
        detections = detector.detect_persons(frame)
        tracks = tracker.track(frame=frame, detections=detections, detector=detector, ts=ts)
        pose_map = pose.estimate(frame=frame, tracks=tracks, ts=ts)

        risk_scores: dict[int, float] = {}
        states: dict[int, str] = {}
        all_signals = []
        for track in tracks:
            pose_track = pose_map.get(
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
            signals, event, point = fsm.update_track(
                camera_id=settings.camera_id,
                track=track,
                pose=pose_track,
                zones=zones.zones,
                ts=ts,
            )
            all_signals.extend(signals)
            risk_scores[track.track_id] = point.risk_score
            states[track.track_id] = point.state
            if event is not None:
                events.append(
                    {
                        "ts": event.ts_trigger.isoformat(),
                        "track_id": event.track_id,
                        "event_type": event.event_type,
                        "risk": event.risk_score_at_trigger,
                        "explanation": event.short_explanation,
                    }
                )

        annotated = annotate_frame(
            frame=frame,
            tracks=tracks,
            zones=zones.zones,
            risk_scores=risk_scores,
            track_states=states,
            pose_map=pose_map,
            event_labels=[e["event_type"] for e in events[-3:]],
        )
        writer.write(annotated)

        frame_idx += 1
        if args.max_frames > 0 and frame_idx >= args.max_frames:
            break
        ok, frame = cap.read()

    cap.release()
    writer.release()

    events_path = out_path.with_suffix(".events.json")
    events_path.write_text(json.dumps(events, indent=2), encoding="utf-8")
    logger.info(f"Done. Frames={frame_idx}, output={out_path}, events={events_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
