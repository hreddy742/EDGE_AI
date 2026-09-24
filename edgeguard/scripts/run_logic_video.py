import argparse
from collections import deque
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
from src.fusion.global_identity import GlobalIdentityResolver
from src.rules.risk import RiskEngine
from src.rules.theft_fsm import TheftRiskFSM
from src.rules.theft_state_machine import TheftStateMachine
from src.rules.zones import is_point_in_zone, load_zone_config
from src.vision.annotator import annotate_frame
from src.vision.detector import YOLODetector
from src.vision.pose import PoseEstimator, PoseKeypoints
from src.vision.reid import ReIDEmbedder
from src.vision.tracker import PersonTracker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EdgeGuard logic pipeline on a local video with rich overlays.")
    parser.add_argument("--video", required=True, help="Input video path")
    parser.add_argument("--zones", default="config/zones.sample.json", help="Zones JSON path")
    parser.add_argument("--output", default="data/debug/logic_annotated_output.mp4", help="Annotated output video")
    parser.add_argument("--max-frames", type=int, default=0, help="Optional frame cap (0 = no cap)")
    parser.add_argument("--counter-buffer-sec", type=float, default=2.0, help="Missing persistence buffer at counter")
    return parser.parse_args()


def _resolve_customer_id(frame, track, ts: datetime, reid: ReIDEmbedder, resolver: GlobalIdentityResolver, camera_id: str) -> str:
    x1, y1, x2, y2 = [int(v) for v in track.bbox]
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w - 1, x2), min(h - 1, y2)
    crop = frame[y1:y2, x1:x2] if (x2 > x1 and y2 > y1) else None
    emb: list[float] = []
    if crop is not None and crop.size > 0:
        result = reid.embed(crop)
        if result is not None:
            emb = result.embedding
    height_px = float(max(1.0, y2 - y1))
    return resolver.match_or_create(
        camera_id=camera_id,
        local_track_id=track.track_id,
        embedding=emb,
        ts=ts,
        height_px=height_px,
    )


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
    rule_fsm = TheftRiskFSM(settings=settings)
    state_machine = TheftStateMachine()
    risk_engine = RiskEngine()
    resolver = GlobalIdentityResolver()
    reid = ReIDEmbedder(enabled=True)

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), max(1, settings.frame_fps), (w, h))

    track_active_items: dict[str, list[str]] = {}
    track_item_counter: dict[str, int] = {}
    last_pick_ts: dict[str, datetime] = {}
    pick_history: dict[str, deque[datetime]] = {}
    prev_in_exit: dict[str, bool] = {}
    counter_state: dict[str, dict] = {}
    events: list[dict] = []

    def next_item_id(customer_id: str) -> str:
        idx = track_item_counter.get(customer_id, 0) + 1
        track_item_counter[customer_id] = idx
        return f"{customer_id}:item:{idx}"

    def allow_decay(customer_id: str) -> bool:
        basket = state_machine.get_basket(customer_id)
        return len(basket.items_concealed) == 0 and not state_machine.get_mismatch_unresolved(customer_id)

    frame_idx = 0
    while ok and frame is not None:
        ts = datetime.now(timezone.utc)
        detections = detector.detect_persons(frame)
        tracks = tracker.track(frame=frame, detections=detections, detector=detector, ts=ts)
        pose_map = pose.estimate(frame=frame, tracks=tracks, ts=ts)

        risk_scores: dict[int, float] = {}
        states: dict[int, str] = {}
        overlay_metrics: dict[int, dict] = {}
        event_labels: list[str] = []

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
            signals, _, point = rule_fsm.update_track(
                camera_id=settings.camera_id,
                track=track,
                pose=pose_track,
                zones=zones.zones,
                ts=ts,
            )
            customer_id = _resolve_customer_id(frame, track, ts, reid, resolver, settings.camera_id)
            basket = state_machine.get_basket(customer_id)
            active_items = track_active_items.setdefault(customer_id, [])

            # Put-back before concealment.
            shelf_interactions = [s for s in signals if s.signal_type == "SHELF_INTERACTION"]
            actions: list[tuple[str, str | None]] = []
            if shelf_interactions and basket.items_in_hand:
                lp = last_pick_ts.get(customer_id)
                if lp and (ts - lp).total_seconds() > settings.conceal_window_sec:
                    item_to_put_back = sorted(list(basket.items_in_hand))[0]
                    actions.append(("PUT_BACK", item_to_put_back))
                    if item_to_put_back in active_items:
                        active_items.remove(item_to_put_back)
            else:
                for signal in signals:
                    if signal.signal_type == "SHELF_INTERACTION":
                        lp = last_pick_ts.get(customer_id)
                        if lp is None or (ts - lp).total_seconds() >= settings.conceal_window_sec:
                            item_id = next_item_id(customer_id)
                            active_items.append(item_id)
                            last_pick_ts[customer_id] = ts
                            actions.append(("PICK", item_id))
                            history = pick_history.setdefault(customer_id, deque(maxlen=8))
                            history.append(ts)
                            if len(history) >= 2 and (history[-1] - history[-2]).total_seconds() <= 2.5:
                                actions.append(("RAPID_MULTI_PICK", None))
                    elif signal.signal_type == "HAND_TO_BAG" and active_items:
                        actions.append(("ON_COUNTER", active_items[-1]))
                    elif signal.signal_type == "HAND_TO_POCKET" and active_items:
                        actions.append(("CONCEAL_POCKET", active_items[-1]))

            for action_type, item_id in actions:
                state_events = state_machine.apply(customer_id=customer_id, signal_type=action_type, item_id=item_id, ts=ts)
                for sev in state_events:
                    if sev.risk_delta != 0:
                        score = risk_engine.apply_delta(
                            customer_id=customer_id,
                            delta=sev.risk_delta,
                            reason=sev.event_type,
                            now=ts,
                            allow_decay=allow_decay(customer_id),
                        )
                    else:
                        score = risk_engine.decay(customer_id=customer_id, now=ts, allow_decay=allow_decay(customer_id))
                    events.append(
                        {
                            "ts": ts.isoformat(),
                            "track_id": track.track_id,
                            "customer_id": customer_id,
                            "event_type": sev.event_type,
                            "risk": round(score, 2),
                            "item_ids": sev.involved_item_ids,
                        }
                    )
                    event_labels.append(sev.event_type)

            # Counter instant check with 2-second missing buffer.
            counter_polygon = zones.zones.get("counter_zone", []) or zones.zones.get("checkout_zone", [])
            in_counter = bool(counter_polygon and is_point_in_zone(track.centroid, counter_polygon))
            cstate = counter_state.setdefault(customer_id, {"checkout_started": False, "missing_timer_start_ts": None})

            hand_count = len(basket.items_in_hand)
            hidden_count = len(basket.items_concealed)
            counter_count = len(basket.items_on_counter)
            seen_now = counter_count + hand_count
            expected = counter_count + hand_count + hidden_count
            missing = max(0, expected - seen_now)

            if in_counter and counter_count > 0:
                cstate["checkout_started"] = True

            if in_counter and cstate["checkout_started"]:
                if missing > 0:
                    if cstate["missing_timer_start_ts"] is None:
                        cstate["missing_timer_start_ts"] = ts
                    elapsed = (ts - cstate["missing_timer_start_ts"]).total_seconds()
                    if elapsed >= args.counter_buffer_sec and not state_machine.get_mismatch_unresolved(customer_id):
                        state_machine.apply(customer_id=customer_id, signal_type="COUNTER_MISMATCH", item_id=None, ts=ts)
                        score = risk_engine.apply_delta(customer_id, 20.0, "COUNTER_MISMATCH", ts, allow_decay=False)
                        events.append(
                            {
                                "ts": ts.isoformat(),
                                "track_id": track.track_id,
                                "customer_id": customer_id,
                                "event_type": "COUNTER_MISMATCH",
                                "risk": round(score, 2),
                                "missing_count": missing,
                                "missing_item_ids": sorted(list(basket.items_concealed)),
                            }
                        )
                        event_labels.append("COUNTER_MISMATCH")
                else:
                    cstate["missing_timer_start_ts"] = None
                    if state_machine.get_mismatch_unresolved(customer_id):
                        state_machine.apply(customer_id=customer_id, signal_type="COUNTER_RECONCILED", item_id=None, ts=ts)
                        score = risk_engine.apply_delta(customer_id, -10.0, "COUNTER_RECONCILED", ts, allow_decay=False)
                        events.append(
                            {
                                "ts": ts.isoformat(),
                                "track_id": track.track_id,
                                "customer_id": customer_id,
                                "event_type": "COUNTER_RECONCILED",
                                "risk": round(score, 2),
                            }
                        )
                        event_labels.append("COUNTER_RECONCILED")

            # Exit crossing logic.
            exit_polygon = zones.zones.get("exit_zone", [])
            in_exit = bool(exit_polygon and is_point_in_zone(track.centroid, exit_polygon))
            prev = prev_in_exit.get(customer_id, False)
            prev_in_exit[customer_id] = in_exit
            if in_exit and not prev:
                state_events = state_machine.apply(customer_id=customer_id, signal_type="EXIT", item_id=None, ts=ts)
                for sev in state_events:
                    score = risk_engine.apply_delta(customer_id, sev.risk_delta, sev.event_type, ts, allow_decay=False)
                    events.append(
                        {
                            "ts": ts.isoformat(),
                            "track_id": track.track_id,
                            "customer_id": customer_id,
                            "event_type": sev.event_type,
                            "risk": round(score, 2),
                        }
                    )
                    event_labels.append(sev.event_type)

            current_risk = risk_engine.decay(customer_id=customer_id, now=ts, allow_decay=allow_decay(customer_id))
            risk_scores[track.track_id] = current_risk
            states[track.track_id] = point.state
            overlay_metrics[track.track_id] = {
                "global_customer_id": customer_id,
                "hand_count": hand_count,
                "hidden_count": hidden_count,
                "counter_count": counter_count,
                "seen_now": seen_now,
                "expected": expected,
                "missing": missing,
                "is_counter_mode": in_counter,
                "mismatch_unresolved": state_machine.get_mismatch_unresolved(customer_id),
            }

        annotated = annotate_frame(
            frame=frame,
            tracks=tracks,
            zones=zones.zones,
            risk_scores=risk_scores,
            track_states=states,
            pose_map=pose_map,
            overlay_metrics=overlay_metrics,
            event_labels=event_labels[-4:],
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
