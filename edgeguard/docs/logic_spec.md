# EdgeGuard Retail Theft Monitoring Logic Specification

## A) Problem Understanding

> Production guardrail: cross-camera ReID in the current codebase is experimental and disabled by default (`CROSS_CAMERA_REID_ENABLED=false`). Do not treat cross-camera identity continuity as production-ready until a validated metric-learning model is integrated.

### Why classic detection fails
- Theft is temporal, not a single-frame class. A reliable decision depends on ordered actions: `SHELF -> HAND -> CONCEAL/BAG/CART -> CHECKOUT/EXIT`.
- Person and item ownership must be maintained over time. Frame-by-frame detections without identity memory cannot reconcile "who picked what."
- Multi-camera stores require continuity. A customer can pick in camera A, conceal in B, and exit in C. Without global identity stitching, each camera looks benign in isolation.
- Checkout reconciliation is mandatory. Suspicion depends on comparison between possessed/concealed items and what was scanned/paid.

### Success criteria
- Every person has stable `global_customer_id` from entry to exit.
- Every picked item gets `global_item_id` and owner association.
- Per-customer basket state always maintains:
  - `visible_in_hand_count`
  - `concealed_count`
  - `returned_to_shelf_count`
  - `paid_count` (if POS connected)
  - `unpaid_concealed_count`
- On checkout or exit, system emits:
  - `risk_score` (not absolute theft)
  - explanation chain (events + confidence)
  - evidence clips per suspicious segment

## B) System Overview

### Pipeline
1. Per-camera vision:
- Person detect + track (local track IDs)
- Pose extraction (wrists/hips/torso)
- Item detection (generic retail objects + bag/hoodie proxies)
- Short-term item tracking
- Interaction signals (hand-item-shelf/cart/bag/scanner zone)

2. Cross-camera fusion:
- Global customer identity from ReID + handoff priors + timing
- Global item continuity while visible; status-based inference when concealed

3. Store reasoning engine:
- Per-customer basket state machine
- Event generation
- Risk accumulation with decay
- Alert policy at checkout/exit

4. POS reconciliation (optional, V2):
- Link checkout session to `global_customer_id`
- Compare paid set/count against possessed/concealed set/count

5. Evidence subsystem:
- Rolling camera buffers
- Clip extraction for PICK, CONCEAL, NONSCAN, EXIT_MISMATCH

### Service/module architecture
- `apps/api`: query and control plane
- `apps/ui`: operator dashboard
- `src/vision`: detection/tracking/pose/ReID primitives
- `src/fusion`: global identity stitching
- `src/rules`: association, state machine, risk model
- `src/evidence`: clip buffering/writing
- `src/store`: persistence models + CRUD
- `src/pipeline`: orchestration per camera + store-level aggregator

## C) Data Model

### Entity: CustomerTrack (global)
- `global_customer_id: UUID`
- `current_camera_id: str`
- `per_camera_track_ids: dict[str, int]`
- `entry_time: datetime`
- `last_seen_time: datetime`
- `current_zone: str`
- `trajectory_summary: dict[str, float]` (seconds in each zone)
- `appearance_embedding: list[float]`
- `last_embedding_update_ts: datetime`
- `risk_score_current: float`
- `basket_state: BasketState`
- `evidence_links: list[str]`

### Entity: ItemTrack
- `global_item_id: UUID`
- `item_class: str`
- `current_status: ON_SHELF | IN_HAND | IN_CART | CONCEALED | RETURNED | PAID | LOST`
- `owner_customer_id: UUID | null`
- `last_seen_camera_id: str`
- `last_seen_bbox: tuple[float, float, float, float] | null`
- `first_pick_time: datetime | null`
- `last_status_change_time: datetime`
- `confidence: float`
- `evidence_clips: list[str]`
- `disappearance_reason: POCKET | BAG | HOODIE | OCCLUSION | UNKNOWN | null`

### Entity: BasketState
- `items_in_hand: set[str]`
- `items_in_cart: set[str]`
- `items_concealed: set[str]`
- `items_returned: set[str]`
- `items_paid: set[str]` or `paid_count: int`
- `inferred_total_possessed: set[str] = in_hand ∪ in_cart ∪ concealed`
- `unpaid_suspected: set[str] = possessed - paid`
- counters:
  - `hand_count`
  - `concealed_count`
  - `paid_count`
  - `unpaid_count`

### Entity: Event (audit log)
- `event_id: str`
- `ts_start: datetime`
- `ts_end: datetime`
- `ts_trigger: datetime`
- `customer_id: str`
- `camera_id: str`
- `event_type: PICK | PUT_BACK | CONCEAL_POCKET | CONCEAL_BAG | CONCEAL_HOODIE | TRANSFER_TO_CART | EXIT_WITH_UNPAID | CHECKOUT_MISMATCH | MULTI_ITEM_CONCEALMENT | SUSPICIOUS_HANDLING | CAMERA_HANDOFF`
- `involved_item_ids: list[str]`
- `risk_delta: float`
- `explanation: str`
- `clip_ids: list[str]`

## D) Zones and Store Map

### Configurable polygons
- `entry_zone`
- `exit_zone`
- `shelf_zones[]`
- `checkout_zones[]`
- `bagging_zones[]`
- `scanner_zones[]`
- `cart_zones[]` (optional)

### Camera adjacency graph
- Directed graph: `camera_a -> camera_b` with:
  - `transition_min_sec`
  - `transition_max_sec`
  - `expected_probability`
  - `overlap_zone_ids` (if overlapping fields of view)
- Used for handoff gating in global identity matching.

## E) Core Logic for Required Scenarios

1. Pick, visible, paid, exit:
- State: `ON_SHELF -> IN_HAND -> PAID -> EXIT`
- Risk rises slightly at pick, reduced at pay, closes at exit.

2. Pick 2, conceal 1, pay 1:
- Concealed item remains unpaid after checkout reconciliation.
- Emit `CHECKOUT_MISMATCH` then `EXIT_WITH_UNPAID` if exiting.

3. Pick then put-back:
- Confirm return by shelf-zone re-entry + static dwell.
- Item removed from possessed set; risk reduced.

4. Pick then cart transfer:
- Hand association breaks; item enters cart zone and persists.
- Status transitions to `IN_CART`.

5. Occlusion disappearance:
- If weak conceal evidence: mark `LOST`, low risk increment, no hard alert.
- Require corroboration (exit mismatch or later conceal cue).

6. Multi-camera continuity:
- Global customer ID maintained by ReID + transition-time gating.
- Item association stays tied to owner across camera handoffs.

7. Pays all:
- At checkout, `items_paid == possessed` and no concealed unpaid.
- Emit closeout summary and reset risk to baseline.

8. Self-checkout non-scan:
- Item moves from hand/cart to bagging zone without scanner evidence.
- Raise `SELF_CHECKOUT_NONSCAN`, escalate at exit.

9. Group interaction/handoff:
- If item passes near wrists between two customers, ownership transfer event created.
- Old owner loses item, new owner gains item with confidence.

10. Drop/abandon away from shelf:
- Item leaves hand and appears in non-shelf floor/unknown zone.
- Mark `LOST`/`UNKNOWN`, moderate risk, human review recommendation.

## F) Algorithms and Pseudocode

### F1) Customer tracking per camera
```python
for frame in camera_stream:
    person_dets = person_detector.detect(frame)
    person_tracks = person_tracker.update(person_dets, frame_ts)
    for t in person_tracks:
        crop = frame[t.bbox]
        emb = reid.embed(crop) if reid.enabled else None
        local_track_memory[t.local_track_id].update(t, emb, frame_ts)
```

### F2) Global customer identity
```python
def match_to_global(local_track, camera_id, ts):
    candidates = recently_seen_globals(adjacent_to=camera_id, within_sec=T_HANDOFF)
    best = None
    best_score = -1.0

    for g in candidates:
        s_emb = cosine(local_track.embedding, g.embedding)
        s_zone = transition_score(g.last_camera_id, camera_id, g.last_seen, ts)
        s_geom = size_similarity(local_track.height_px, g.last_height_px)
        score = w1*s_emb + w2*s_zone + w3*s_geom
        if score > best_score:
            best, best_score = g, score

    if best and best_score >= TH_GLOBAL_MATCH:
        return best.global_customer_id
    return create_new_global_customer(local_track, camera_id, ts)
```

### F3) Item detection and tracking (MVP approximation)
```python
def detect_item_candidates(frame, wrists, shelf_zones):
    dets = yolo_item_detector.detect(frame, classes=GENERIC_ITEM_CLASSES + BAG_CLASSES)
    near_hand = [d for d in dets if min_dist(center(d), wrists) < HAND_ITEM_DIST]
    shelf_motion = foreground_blobs_near_shelf(frame, shelf_zones)  # optional
    return merge_candidates(near_hand, shelf_motion)

for frame in stream:
    candidates = detect_item_candidates(...)
    item_tracks = item_tracker.update(candidates, ts)  # IOU + optional appearance
```

### F4) Hand-item ownership association
```python
for customer in customers_in_frame:
    for item in visible_items:
        d = wrist_item_distance(customer.wrists, item.bbox)
        if d < HAND_GRAB_DISTANCE:
            association_buffer[(customer.id, item.id)].increment()
        else:
            association_buffer[(customer.id, item.id)].decrement()

        if association_buffer[(customer.id, item.id)].value >= ASSOC_N_FRAMES:
            assign_owner(item.id, customer.id)
            set_status(item.id, "IN_HAND")
            emit_event("PICK", customer.id, [item.id])
```

### F5) Shelf pick validation
```python
def is_valid_pick(customer, item, ts):
    cond1 = customer.centroid in any_shelf_zone
    cond2 = wrist_entered_shelf_zone(customer.id, within_sec=X_PICK_WINDOW)
    cond3 = item.associated_to(customer.id) and appeared_within_sec(item.id, X_PICK_WINDOW)
    cond4 = moved_away_from_shelf(item.id, min_distance=SHELF_DEPART_DIST)
    return cond1 and cond2 and cond3 and cond4
```

### F6) Put-back detection
```python
if item.status in {"IN_HAND", "IN_CART"} and item.disassociated:
    if item.bbox in shelf_zone and item.static_for_frames >= M_STATIC_FRAMES:
        set_status(item.id, "RETURNED")
        basket.remove_possessed(item.id)
        emit_event("PUT_BACK", owner_id, [item.id], risk_delta=-RISK_PUT_BACK)
```

### F7) Concealment detection
```python
conceal_regions = build_regions_from_pose(person_bbox, keypoints, bag_bbox)

if item.status == "IN_HAND":
    overlap = region_overlap(item.bbox, conceal_regions)
    wrist_dive = wrist_velocity_into_region(customer.wrists, conceal_regions)
    vanished = item.missing_for_frames >= K_MISSING_AFTER_CONCEAL

    if (overlap and vanished) or (wrist_dive and vanished):
        reason = infer_reason(overlap_region, bag_detected, torso_shape)
        set_status(item.id, "CONCEALED")
        item.disappearance_reason = reason
        basket.items_concealed.add(item.id)
        emit_event(f"CONCEAL_{reason}", customer.id, [item.id], risk_delta=RISK_CONCEAL)
```

### F8) Uncertainty / occlusion handling
```python
if item.status == "IN_HAND" and item.missing:
    conceal_strength = conceal_signal_strength(customer, item)
    if conceal_strength < TH_CONCEAL_WEAK:
        set_status(item.id, "LOST")
        add_uncertainty_flag(item.id, reason="OCCLUSION")
        risk.add(customer.id, RISK_LOST_UNCERTAIN)
        require_confirmation(customer.id, triggers=["exit", "checkout_mismatch"])
```

### F9) Checkout reconciliation
```python
def reconcile_checkout(customer_id, pos_payload=None):
    basket = state[customer_id].basket
    possessed = basket.items_in_hand | basket.items_in_cart | basket.items_concealed

    if pos_payload is None:
        if basket.items_concealed:
            risk.add(customer_id, RISK_UNPAID_CONCEALED_NO_POS)
        return

    if pos_payload.item_ids:
        paid_set = set(pos_payload.item_ids)
        unpaid = possessed - paid_set
    else:
        unpaid_count = max(0, len(possessed) - pos_payload.paid_count)
        unpaid = pick_unpaid_candidates(possessed, unpaid_count)

    if unpaid:
        emit_event("CHECKOUT_MISMATCH", customer_id, list(unpaid), risk_delta=RISK_CHECKOUT_MISMATCH)
```

### F10) Exit decision logic
```python
def on_exit_crossing(customer_id):
    basket = state[customer_id].basket
    unpaid = basket.unpaid_suspected()
    score = risk.current(customer_id)

    if unpaid or score >= TH_RED_ALERT:
        emit_event("EXIT_WITH_UNPAID", customer_id, list(unpaid), risk_delta=RISK_EXIT_WITH_UNPAID)
        trigger_alert(customer_id, level=severity_from_score(score), explanation=build_explanation(customer_id))
    close_session(customer_id)
```

### F11) Evidence clip generation
```python
# camera worker maintains ring buffer of encoded frames
ring_buffer[camera_id].append(frame_ts, jpeg_bytes)

def capture_clip(camera_id, ts_start, ts_end, pre=5, post=5):
    clip_start = ts_start - seconds(pre)
    clip_end = ts_end + seconds(post)
    frames = ring_buffer[camera_id].slice(clip_start, clip_end)
    clip_path = write_mp4(frames)
    return clip_path

on_event(event):
    clip = capture_clip(event.camera_id, event.ts_start, event.ts_end)
    link_clip(event.event_id, clip)
```

## G) Risk Scoring Model

### Additive weights (initial defaults)
- `PICK`: +1
- `MULTI_PICK_SHORT_WINDOW`: +1 per extra item
- `CONCEAL_POCKET`: +6
- `CONCEAL_BAG`: +6
- `CONCEAL_HOODIE`: +6
- `LOST_UNCERTAIN`: +2
- `EXIT_AFTER_CONCEALMENT`: +6
- `CHECKOUT_MISMATCH`: +10
- `PUT_BACK_CONFIRMED`: -2
- `PAY_ALL_CONFIRMED`: set to 0 and close session

### Alert bands
- Yellow/watch: `score >= 8`
- Red/alert: `score >= 12`
- Critical: `score >= 16`

### Decay
- Option A: multiplicative `score *= 0.98` each second without new risk events.
- Option B: subtractive `score -= 1` every 15 seconds (floor at 0).
- Recommended MVP default: subtractive for interpretability.

## H) Build Evolution

### MVP (single camera, prerecorded video)
- Per-frame person detection/tracking
- Pose keypoints
- Basic item candidate detection near hands
- Pick/put-back/conceal/uncertain state machine
- Risk scoring + evidence clips
- API/UI for event timeline and per-customer risk

### V1 (multi-camera)
- ReID embedding and global identity stitching
- Camera adjacency handoff gating
- Cross-camera event timeline merge
- Store-level customer sessions

### V2 (POS + self-checkout maturity)
- POS connector (paid count or item IDs)
- Checkout mismatch logic
- Self-checkout non-scan via scanner/bagging sequence
- Better item categories and transfer handling

## I) Implementation Task List with Module Mapping

### Vision
- `src/vision/person_detector.py`: person detector abstraction + Ultralytics backend.
- `src/vision/person_tracker.py`: local track lifecycle, lost/active logic.
- `src/vision/reid.py`: embedding extraction and cosine matcher.
- `src/vision/item_detector.py`: generic item/bag detector (MVP approximation).
- `src/vision/item_tracker.py`: item track linking + confidence aging.
- `src/vision/pose.py`: extend current pose outputs for conceal-region logic.

### Rules / Fusion / Evidence
- `src/rules/association.py`: hand-item ownership + transfer.
- `src/rules/theft_state_machine.py`: basket transitions and event emission.
- `src/rules/risk.py`: additive + decay risk engine.
- `src/fusion/global_identity.py`: global customer matching with adjacency gating.
- `src/evidence/clip_writer.py`: ring buffer + mp4 writer + event linkage.

### Store and API/UI
- `src/store/db.py`: table lifecycle and migration hooks.
- `src/store/models.py`: CustomerTrack, ItemTrack, BasketSnapshot, Event, ClipLink.
- `src/store/crud.py`: upsert/get/query for customers/items/events/clips.
- `apps/api`: endpoints
  - `GET /customers`
  - `GET /customers/{id}`
  - `GET /items`
  - `GET /events`
  - `GET /clips/{clip_id}`
  - `GET /customers/{id}/explain`
- `apps/ui`: dashboard with per-customer counts and red/yellow indicators.

### Configurable tuning knobs
- Distances:
  - `HAND_GRAB_DISTANCE_PX`
  - `HAND_TO_BAG_DISTANCE_PX`
  - `SHELF_DEPART_DISTANCE_PX`
- Temporal:
  - `ASSOC_N_FRAMES`
  - `K_MISSING_AFTER_CONCEAL`
  - `PUT_BACK_STATIC_FRAMES`
  - `T_HANDOFF_SEC`
- Matching thresholds:
  - `TH_GLOBAL_MATCH`
  - `TH_CONCEAL_WEAK`
  - `TH_ITEM_TRACK_LINK`
- Risk:
  - per-event weights
  - decay mode/rate
  - alert thresholds

## J) Recommended Open-Source Stack

### Person detector/tracker
- Detector: Ultralytics YOLOv8/YOLO11 (`person` class)
- Tracker: ByteTrack (Ultralytics integration) or DeepSORT fallback

### ReID
- `torchreid` (OSNet variants) for person embeddings
- Alternative: FastReID (heavier, more accurate with tuning)

### Pose
- Ultralytics YOLOv8 pose for wrists/hips
- Optional: MediaPipe Pose for CPU-friendly fallback

### Item detection/tracking
- MVP pragmatic approach:
  - Generic COCO classes (`bottle`, `backpack`, `handbag`) + near-hand candidate logic
  - IOU/appearance tracker for short lifespan item tracks
- V1/V2 improvement:
  - Fine-tuned detector on retail SKUs or package archetypes
  - Stronger appearance embeddings for item re-association

### Honest limitation statement
- Reliable SKU-level item identity in dense shelves is hard without store-specific training data and camera placement constraints.
- MVP should focus on robust person-centric possession/concealment reasoning and risk output, not perfect item classification.
