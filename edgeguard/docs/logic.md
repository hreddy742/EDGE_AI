# EdgeGuard Theft-Risk Logic

## Summary
EdgeGuard now uses a temporal risk pipeline rather than single-frame theft classification.

Per sampled frame:
1. Person detection (YOLOv8 detect).
2. Person tracking (ByteTrack via ultralytics; IOU fallback).
3. Pose estimation (YOLOv8 pose) for wrists and hips.
4. Signal extraction and per-track FSM updates.
5. Risk score update and event trigger checks.
6. Overlay, DB persistence, and optional clip generation.

## Track State Machine
- `BROWSING`
- `NEAR_SHELF`
- `SHELF_INTERACTION`
- `PICK_SUSPECTED`
- `POSSIBLE_CONCEALMENT`
- `CONFIRMED_CONCEALMENT` (reserved in MVP)
- `HIGH_RISK_EXIT`
- `CLEARED`

## Signals
- `NEAR_SHELF`: centroid inside `shelf_zone`
- `SHELF_INTERACTION`: wrists in shelf zone for N consecutive frames
- `HAND_TO_POCKET`: hand-to-hip distance drops soon after shelf interaction
- `HAND_TO_BAG`: wrist enters bagging zone
- `EXIT_AFTER_CONCEALMENT`: centroid crosses exit zone after concealment sequence
- `NONSCAN_BAGGING`: bagging seen without recent scanner gesture (self-checkout mode)
- `SPEED_SPIKE`: high velocity around exit

## Risk Scoring
Weighted additive risk model (per track), with small decay each frame.
Default weights:
- `SHELF_INTERACTION`: +2
- `HAND_TO_POCKET`: +4
- `HAND_TO_BAG`: +3
- `EXIT_AFTER_CONCEALMENT`: +6
- `REPEATED_SHELF_INTERACTION`: +2
- `NONSCAN_BAGGING`: +5
- `SPEED_SPIKE`: +1.5

Event trigger:
- `risk_score >= RISK_THRESHOLD`
- cooldown not active for that track

Important: events represent **theft risk**, not theft certainty.

## Event Types
- `POSSIBLE_CONCEALMENT`
- `HIGH_RISK_EXIT`
- `SELF_CHECKOUT_NONSCAN` (when `MODE=self_checkout`)

## Persistence
- `events`: trigger-level risk events with explanation and snapshot
- `signals`: per-frame / per-event signal records
- `track_timeline`: risk score timeline and state progression

## Debugging
- Optional debug CSV dump for one track using `DEBUG_TRACK_ID`.
- Use `scripts/run_video.py` to quickly iterate on thresholds/zones.
