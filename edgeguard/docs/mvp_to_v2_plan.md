# EdgeGuard MVP -> V1 -> V2 Implementation Plan

## 1) Delivery Strategy

- Build a practical 4-camera sellable baseline first.
- Keep strict separation between:
  - perception (detections/signals)
  - fusion (global customer IDs)
  - reasoning (item ledger + risk + reconciliation)
  - evidence lifecycle (retention/deletion policy)

## 2) MVP Scope (sellable baseline)

Target:
- 1 `ENTRY_EXIT`, 2 `AISLE`, 1 `COUNTER` via RTSP.
- One `GlobalCustomerID` per session.
- Item ledger transitions: `PICK`, `PUT_BACK`, `CONCEAL_*`, `ON_COUNTER`.
- Counter mismatch detection.
- Exit close decision and clip retention policy.

### MVP tasks (ordered)
1. Camera config + role assignment UI/API.
2. Zone drawing + persistence per role.
3. Per-camera worker stability (RTSP reconnect, frame sampling, queueing).
4. Local tracking + embeddings output per camera.
5. Central fusion engine with adjacency/time gating.
6. Item transition engine in AISLE roles.
7. Counter presented-item counting/matching.
8. Session manager close logic at ENTRY_EXIT.
9. Clip lifecycle statuses: TEMP/KEEP/DELETE_PENDING/DELETED.
10. Background cleanup worker.
11. Alert panel with risk score, missing count, clip links.

### MVP acceptance criteria
- 85%+ session continuity through store path on test set.
- Correct basket count updates for pick/put-back scenarios.
- Counter mismatch detected when subset presented.
- Cleared sessions mark TEMP clips DELETE_PENDING at close.
- Alert sessions retain KEEP clips.

## 3) V1 Scope (accuracy and robustness)

Goals:
- Improve identity continuity and reduce ID switches in crowds.
- Improve item-to-counter matching quality.
- Better ownership transfer handling (handoff between customers).

### V1 tasks
1. Stronger ReID model and camera-pair calibration.
2. Multi-hypothesis identity resolution under ambiguity.
3. Better item appearance signatures for short-term re-association.
4. Handoff detector + owner transfer confidence rules.
5. Counter disambiguation under multiple simultaneous customers.
6. Reviewer workflow: mark false positives, override retention.

### V1 metrics
- Lower ID-switch rate in crowded scenes.
- Reduced false counter mismatch rate.
- Improved precision of concealment events with confidence thresholds.

## 4) V2 Scope (POS + self-checkout)

Goals:
- Integrate POS counts (minimum) and item IDs (if available).
- Add self-checkout non-scan logic.

### V2 tasks
1. POS connector adapters (REST/webhook/file feed).
2. Checkout session join logic (counter-time/customer binding).
3. POS reconciliation rules:
  - `paid_count` vs `possessed_count`
  - optional item-ID level match.
4. Self-checkout scanner-zone/bagging-zone sequence detection.
5. Policy tuning for false positives and legal retention compliance.

### V2 metrics
- Mismatch detection precision with POS ground truth.
- Reduced unresolved alert volume after paid verification.

## 5) Tuning Strategy

## 5.1 Dataset creation
- Collect representative clips per store layout:
  - normal purchase flows
  - concealment styles
  - crowding/occlusion cases
  - counter congestion

## 5.2 Parameter tuning loop
1. Run fixed replay set.
2. Measure event precision/recall and mismatch accuracy.
3. Adjust thresholds:
  - pick frames (`N`, `M`)
  - conceal disappear frames (`K`)
  - global match threshold
  - risk thresholds
4. Re-run, compare to previous baseline.

## 5.3 Conservative defaults
- Bias toward uncertainty (`LOST_UNCERTAIN`) over aggressive theft claims.
- Require multi-signal corroboration for RED-level alerts.

## 6) Testing Plan

### Unit tests
- Basket transitions (pick/return/conceal/counter/pay/exit).
- Risk scoring and decay.
- Clip lifecycle transitions and cleanup.
- Session close policy branching.

### Integration tests
- Multi-camera handoff continuity.
- Counter mismatch then resolved scenario.
- Exit unresolved mismatch scenario.
- Cleared session clip deletion path.

### Replay tests
- Prerecorded multi-camera store sessions with expected outputs.

## 7) Deployment and Ops Plan

1. Health checks per camera worker.
2. Backpressure control for slow streams.
3. DB retention indexes and periodic cleanup.
4. Alert dashboard with clip playback.
5. Monitoring:
  - camera online/offline
  - queue lag
  - clip write failures
  - session close counts (CLEARED vs ALERT)

## 8) Risk Controls

- No “theft confirmed” wording in product outputs.
- Always include uncertainty reason when evidence is weak.
- Keep auditable event history even after clip file deletion.
