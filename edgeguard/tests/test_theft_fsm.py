from datetime import datetime, timedelta

from src.core.config import Settings
from src.rules.theft_fsm import TheftRiskFSM
from src.vision.pose import PoseKeypoints
from src.vision.tracker import PersonTrack


def _settings() -> Settings:
    return Settings(
        n_frames_hand_in_shelf=2,
        conceal_window_sec=4,
        risk_threshold=3,
        event_cooldown_seconds=10,
    )


def _track(track_id: int = 1, x: float = 50, y: float = 50) -> PersonTrack:
    return PersonTrack(
        track_id=track_id,
        bbox=(x - 10, y - 10, x + 10, y + 10),
        conf=0.9,
        centroid=(x, y),
        velocity=0.0,
        last_seen_ts=datetime(2026, 1, 1, 12, 0, 0),
    )


def _pose(left_wrist=(60.0, 60.0), right_wrist=(62.0, 62.0), hip_center=(60.0, 120.0), hand_to_hip=90.0) -> PoseKeypoints:
    return PoseKeypoints(
        keypoints=[],
        left_wrist=left_wrist,
        right_wrist=right_wrist,
        hip_center=hip_center,
        available=True,
        hand_to_hip_distance=hand_to_hip,
        hand_speed=4.0,
        ts=datetime(2026, 1, 1, 12, 0, 0),
    )


def test_fsm_shelf_to_possible_concealment_transition() -> None:
    fsm = TheftRiskFSM(_settings())
    zones = {
        "shelf_zone": [(0, 0), (120, 0), (120, 120), (0, 120)],
        "exit_zone": [(300, 0), (400, 0), (400, 100), (300, 100)],
        "bagging_zone": [],
        "scanner_zone": [],
    }
    base = datetime(2026, 1, 1, 12, 0, 0)

    # frame 1: wrist in shelf
    signals1, event1, point1 = fsm.update_track(
        camera_id="cam01",
        track=_track(),
        pose=_pose(hand_to_hip=200.0),
        zones=zones,
        ts=base,
    )
    assert event1 is None
    assert point1.state in {"NEAR_SHELF", "BROWSING", "SHELF_INTERACTION"}
    # NEAR_SHELF is now a state update only — it is no longer emitted as a noisy signal.

    # frame 2: shelf interaction reached
    signals2, event2, point2 = fsm.update_track(
        camera_id="cam01",
        track=_track(),
        pose=_pose(hand_to_hip=200.0),
        zones=zones,
        ts=base + timedelta(seconds=1),
    )
    assert any(s.signal_type == "SHELF_INTERACTION" for s in signals2)
    assert event2 is None

    # frame 3: hand retract + close to hip -> possible concealment
    pose3 = _pose(left_wrist=(180.0, 180.0), right_wrist=(182.0, 180.0), hand_to_hip=70.0)
    _, event3, point3 = fsm.update_track(
        camera_id="cam01",
        track=_track(),
        pose=pose3,
        zones=zones,
        ts=base + timedelta(seconds=2),
    )
    assert point3.state in {"POSSIBLE_CONCEALMENT", "PICK_SUSPECTED", "NEAR_SHELF"}
    assert event3 is not None
    assert event3.event_type in {"POSSIBLE_CONCEALMENT", "HIGH_RISK_EXIT"}


def test_cooldown_blocks_repeated_events() -> None:
    fsm = TheftRiskFSM(_settings())
    zones = {
        "shelf_zone": [(0, 0), (120, 0), (120, 120), (0, 120)],
        "exit_zone": [(0, 0), (120, 0), (120, 120), (0, 120)],
        "bagging_zone": [],
        "scanner_zone": [],
    }
    base = datetime(2026, 1, 1, 12, 0, 0)
    track = _track()
    pose_in_shelf = _pose(left_wrist=(60.0, 60.0), right_wrist=(62.0, 62.0), hand_to_hip=220.0)
    pose_retract = _pose(left_wrist=(180.0, 180.0), right_wrist=(182.0, 180.0), hand_to_hip=60.0)

    # build up shelf interaction first, then concealment gesture to trigger first event
    _, _, _ = fsm.update_track("cam01", track, pose_in_shelf, zones, base)
    _, _, _ = fsm.update_track("cam01", track, pose_in_shelf, zones, base + timedelta(seconds=1))
    _, event1, _ = fsm.update_track("cam01", track, pose_retract, zones, base + timedelta(seconds=2))
    _, event2, _ = fsm.update_track("cam01", track, pose_retract, zones, base + timedelta(seconds=3))
    _, event3, _ = fsm.update_track("cam01", track, pose_retract, zones, base + timedelta(seconds=4))
    assert event1 is not None
    assert event2 is None
    assert event3 is None

    _, event_after, _ = fsm.update_track(
        "cam01",
        track,
        pose_retract,
        zones,
        base + timedelta(seconds=12),
    )
    assert event_after is not None
