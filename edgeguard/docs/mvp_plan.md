# EdgeGuard Build Plan (MVP -> V1 -> V2)

## Scope
- Goal: produce risk-scored theft monitoring from temporal behavior, not binary theft labels.
- Constraint: ship a working single-camera prerecorded-video MVP first, then add multi-camera and POS.

## Phase 0: Foundations (1-2 days)
1. Finalize configs and zones.
- Add defaults for thresholds in `config/` and `.env.example`.
- Validate zone polygons and camera metadata schemas.

2. Data contracts.
- Freeze event payload schema.
- Define customer/item/basket state serialization format.

3. Evidence plumbing baseline.
- Confirm rolling frame buffer and clip writer interfaces.

## Phase 1: MVP Single Camera (3-7 days)
1. Person pipeline.
- Use `src/vision/person_detector.py` and `src/vision/person_tracker.py`.
- Output stable local track IDs and per-track timestamps.

2. Pose and interaction signals.
- Extend `src/vision/pose.py` to output wrist/hip confidence and motion vectors.
- Add `src/rules/association.py` for hand-item linking.

3. Item candidate pipeline.
- Add `src/vision/item_detector.py` and `src/vision/item_tracker.py`.
- Start with generic classes + near-hand filtering.

4. Theft state machine.
- Implement `src/rules/theft_state_machine.py`.
- States: `ON_SHELF, IN_HAND, IN_CART, CONCEALED, RETURNED, LOST, PAID`.
- Emit events: `PICK, PUT_BACK, CONCEAL_*, TRANSFER_TO_CART, SUSPICIOUS_HANDLING`.

5. Risk engine.
- Implement `src/rules/risk.py` with additive weights and decay.
- Add thresholds (yellow/red/critical).

6. Evidence clips.
- Implement `src/evidence/clip_writer.py`.
- For each suspicious event, persist clip with pre/post buffer.

7. API/UI surfacing.
- Expose events/customers/items/clips via API.
- Add UI panel showing per-customer basket counts + risk bands.

8. MVP acceptance tests.
- Scenarios: pick-pay-exit, pick-conceal-exit, pick-put-back, occlusion.
- Verify risk explanations and clips generated for each event.

## Phase 2: V1 Multi-Camera (1-2 weeks)
1. Global identity.
- Implement `src/fusion/global_identity.py`.
- Match local tracks to global IDs with ReID + adjacency-time gating.

2. Camera graph integration.
- Add adjacency probabilities and transition windows to config.
- Emit `CAMERA_HANDOFF` events.

3. Store-level timeline merge.
- Merge per-camera events into one customer session timeline.
- Preserve causal chain from pick to exit across cameras.

4. Multi-camera validation.
- Test camera A pick -> camera B conceal -> camera C exit.
- Measure ID-switch and handoff errors.

## Phase 3: V2 POS and Self-Checkout (1-2 weeks)
1. POS adapter.
- Ingest paid count and optional item IDs by checkout session.
- Link POS sessions to customer IDs by checkout zone + timing.

2. Reconciliation.
- Compare paid vs possessed at checkout completion.
- Emit `CHECKOUT_MISMATCH` and escalate on exit.

3. Self-checkout non-scan.
- Add scanner-zone and bagging-zone sequence logic.
- Detect bagging without scan signal.

4. Operator workflow hardening.
- Add explainability endpoint for event chain.
- Add queue for human review on uncertain `LOST/OCCLUSION` outcomes.

## Module Skeleton Map
- `src/vision/person_detector.py`
- `src/vision/person_tracker.py`
- `src/vision/reid.py`
- `src/vision/item_detector.py`
- `src/vision/item_tracker.py`
- `src/vision/pose.py`
- `src/rules/association.py`
- `src/rules/theft_state_machine.py`
- `src/rules/risk.py`
- `src/fusion/global_identity.py`
- `src/evidence/clip_writer.py`
- `src/store/db.py`
- `src/store/models.py`
- `src/store/crud.py`
- `apps/api/routes_retail.py` (events/customers/items/clips)
- `apps/ui/dashboard_retail.py` (counts + risk bands)

## Tuning Matrix (starting defaults)
- `HAND_GRAB_DISTANCE_PX=60`
- `ASSOC_N_FRAMES=5`
- `K_MISSING_AFTER_CONCEAL=8`
- `PUT_BACK_STATIC_FRAMES=12`
- `TH_GLOBAL_MATCH=0.72`
- `TH_CONCEAL_WEAK=0.45`
- `RISK_YELLOW=8`
- `RISK_RED=12`
- `RISK_CRITICAL=16`

## Delivery Milestones
1. MVP demo on prerecorded video with event timeline and clips.
2. V1 demo with cross-camera continuity and global customer IDs.
3. V2 demo with POS reconciliation and non-scan alerts.
