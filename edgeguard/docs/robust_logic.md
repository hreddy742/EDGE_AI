# EdgeGuard Robust Logic Blueprint

## 1) Product Goal and Constraints

- Output is always **risk**, not theft certainty.
- Must maintain one `GlobalCustomerID` per person across cameras.
- Must maintain per-customer item ledger with explicit `GlobalItemID`.
- Must reconcile at counter (and POS when enabled).
- Must enforce evidence clip retention/deletion at session close.
- Must be practical with open-source models and tunable thresholds.

## 2) Camera Roles and Their Responsibilities

### ENTRY_EXIT camera
- Session open and close boundaries.
- Strong identity anchor at entry and exit crossing lines/polygons.
- Emits `SESSION_START`, `ENTRY_CROSS`, `EXIT_CROSS`.

### AISLE cameras (2x)
- Primary source for `PICK`, `PUT_BACK`, `CONCEAL_*`, `LOST_UNCERTAIN`.
- Tracks shelf interactions and item ownership transitions.

### COUNTER camera
- Detects `ON_COUNTER` presentation.
- Runs mismatch reconciliation (`presented_count` vs `possessed_count`).
- Emits `COUNTER_MISMATCH`, `COUNTER_RESOLVED`, optional POS mismatch.

## 3) Core Data Model

## 3.1 CustomerSession
- `global_customer_id: str` (`CUST-UUID`)
- `session_id: str`
- `current_camera_id: str`
- `per_camera_track_ids: dict[str, int]`
- `entry_ts: datetime`
- `last_seen_ts: datetime`
- `exit_ts: datetime | null`
- `appearance_embedding: list[float]`
- `risk_score_current: float`
- `state: ACTIVE | CLEARED | ALERT_CLOSED`
- `basket_ledger: BasketLedger`
- `mismatch_state: NONE | OPEN | RESOLVED`

## 3.2 ItemRecord
- `global_item_id: str` (`ITEM-UUID`)
- `owner_customer_id: str | null`
- `current_status: ON_SHELF | IN_HAND | IN_CART | CONCEALED | ON_COUNTER | RETURNED | PAID | LOST_UNCERTAIN | RESOLVED`
- `last_seen_camera_id: str | null`
- `last_seen_bbox: tuple[float,float,float,float] | null`
- `first_pick_ts: datetime | null`
- `last_status_change_ts: datetime`
- `disappearance_reason: POCKET | PANTS | HOODIE | SHIRT | BAG | OCCLUSION | UNKNOWN | null`
- `confidence: float`
- `evidence_clips: list[str]`

## 3.3 BasketLedger (per customer)
- `items_in_hand: set[item_id]`
- `items_in_cart: set[item_id]`
- `items_concealed: set[item_id]`
- `items_on_counter: set[item_id]`
- `items_returned: set[item_id]`
- `items_paid: set[item_id]` (or paid_count in POS-count mode)

Derived:
- `possessed = union(in_hand, in_cart, concealed)`
- `unpaid = possessed - paid` (POS mode)
- No POS: `unpaid_proxy = union(concealed, unresolved_mismatch_candidates)`

## 3.4 Event
- `event_id, ts_start, ts_end, ts_trigger`
- `camera_id, local_person_id, global_customer_id`
- `event_type`
- `item_ids: list[str]`
- `risk_delta`
- `explanation`
- `confidence`
- `clip_ids`

## 3.5 Clip
- `clip_id`
- `camera_id`
- `event_id`
- `path`
- `status: TEMP | KEEP | DELETE_PENDING | DELETED`
- `retention_until`

## 4) Global Identity Fusion (multi-camera)

## 4.1 Input
Per-camera emits:
- `local_person_id=(camera_id, track_id)`
- embedding, bbox size, zone, timestamp, camera role

## 4.2 Candidate gating
- Only consider customers seen recently in adjacent cameras based on camera graph.
- Time window `T_HANDOFF_SEC` by edge.
- Optional overlap zones improve confidence.

## 4.3 Match score
`score = w1*reid_cosine + w2*time_score + w3*transition_score + w4*size_score`

If `score >= TH_GLOBAL_MATCH`: reuse customer.
Else: create new `GlobalCustomerID`.

## 4.4 Camera adjacency graph
Example edges:
- `ENTRY_EXIT -> AISLE_1`
- `ENTRY_EXIT -> AISLE_2`
- `AISLE_1 -> COUNTER`
- `AISLE_2 -> COUNTER`
- `COUNTER -> ENTRY_EXIT`

## 5) Per-Camera Perception

1. Person detect+track -> local IDs.
2. Pose -> wrists/hips/torso cues.
3. Item candidates:
- near wrist
- near shelf region change
- near counter stable objects
4. Zone tests by role.
5. Emit normalized low-level signals.

## 6) Item Lifecycle Logic

## 6.1 Pick confirmation (create ItemID)
Conditions all true:
- customer near shelf zone
- wrist in shelf zone for `N` frames
- item candidate appears near wrist or shelf-delta
- candidate moves away with person for `M` frames

On confirm:
- create `ITEM-UUID`
- owner = customer
- status `IN_HAND`
- ledger add to `items_in_hand`
- emit `PICK`
- create clip marker (TEMP)

## 6.2 Put-back confirmation (remove from possessed)
Conditions:
- item currently `IN_HAND` or `IN_CART`
- item enters shelf polygon
- item static for `S` frames
- wrist near during placement then leaves

On confirm:
- status `RETURNED`
- remove item from in_hand/in_cart/concealed/on_counter
- add to `items_returned`
- risk decrement (small)
- emit `PUT_BACK`

## 6.3 Concealment confirmation
Conceal regions:
- pocket/pants near hips
- hoodie/shirt torso
- bag region (detected bag or side proxy)

Trigger for item:
- item is `IN_HAND`
- overlap with conceal region or wrist enters conceal region
- item disappears for `K` frames after interaction

On confirm:
- status `CONCEALED`
- move `IN_HAND -> CONCEALED`
- large risk increase
- emit `CONCEAL_*`
- create KEEP clip (high-value)

## 6.4 Disappearance uncertain
If item disappears with weak conceal evidence:
- status `LOST_UNCERTAIN`
- risk small increase
- no hard alert by itself
- wait for counter mismatch/exit escalation

## 7) Counter Reconciliation Logic

## 7.1 Presented on counter
If object stable in `counter_zone` for `C` frames -> `ON_COUNTER`.
Attempt map to known ItemID by temporal order + hand proximity + appearance.
If unknown, create `presented_unknown` count bucket.

## 7.2 Possessed vs presented (video-only)
- `possessed_count = len(union(in_hand, in_cart, concealed))`
- `presented_count = |items_on_counter| + unknown_presented_count`

If `presented_count < possessed_count`:
- `missing_count = possessed_count - presented_count`
- `missing_items = possessed - presented` (prioritize concealed)
- emit `COUNTER_MISMATCH`, risk +8
- attach clips per missing item (conceal clip preferred, else pick clip)

If later counts match and concealed empty:
- emit `COUNTER_RESOLVED`
- downgrade risk (do not delete clips immediately)

## 7.3 POS mode (optional)
At checkout completion:
- if `paid_count < possessed_count` -> `CHECKOUT_MISMATCH` (KEEP clips)
- if `paid_count == possessed_count` -> `PAID_VERIFIED`

## 8) Exit and Session Close

On ENTRY_EXIT exit crossing:
- ALERT close if:
  - `concealed_count > 0`
  - unresolved mismatch
  - POS unpaid > 0
- Else CLEARED close.

Emit:
- `HIGH_RISK_EXIT` for alert close
- `SESSION_CLEARED` for clean close

## 9) Clip Retention Policy

## 9.1 Creation defaults
- `PICK`: TEMP
- `CONCEAL_*`: KEEP
- `COUNTER_MISMATCH`: KEEP
- `HIGH_RISK_EXIT`: KEEP

## 9.2 Session close decisions
### CLEARED
Video-only:
- concealed_count == 0 and presented_count == possessed_count
POS:
- concealed_count == 0 and paid_count == possessed_count

Action:
- TEMP -> DELETE_PENDING (`retention_until` short, e.g. 24-48h)
- keep lightweight metadata

### ALERT
Any unresolved conceal/mismatch/unpaid/exit alert:
- status KEEP
- set `retention_until` long (e.g. 30 days) or until review resolution

## 9.3 Cleanup job
Periodic worker:
- delete files where `status=DELETE_PENDING and now > retention_until`
- mark `DELETED`
- keep DB audit row

## 10) Uncertainty and Conservative Behavior

- Low confidence detections do not directly trigger red alerts.
- `LOST_UNCERTAIN` requires corroboration.
- Identity ambiguity keeps multiple hypotheses until resolved by counter/exit context.
- Crowding/handoff can downgrade confidence and increase human-review priority.
- Output includes confidence and uncertainty reasons.

## 11) Risk Model

Increments:
- `PICK +1`
- `MULTI_PICK_FAST +1 each extra`
- `CONCEAL_* +6`
- `COUNTER_MISMATCH +8`
- `HIGH_RISK_EXIT +10`
- `LOST_UNCERTAIN +2`

Decrements:
- `PUT_BACK -2`
- `COUNTER_RESOLVED -3`
- `PAID_VERIFIED / ALL_PRESENTED` decay toward green

Thresholds:
- GREEN `< 8`
- YELLOW `8..11`
- RED `>= 12`

Always emit:
- risk score
- explanation chain
- missing count
- clip references

## 12) Key Pseudocode

```python
def process_camera_event(e):
    cust_id = global_fusion.resolve_customer(e.local_person_id, e.embedding, e.camera_id, e.ts)
    session = session_store.get_or_create(cust_id, e.ts)

    if e.type == "PICK_CONFIRMED":
        item_id = new_item_id()
        item_store.create(item_id, owner=cust_id, status="IN_HAND", ts=e.ts)
        ledger[cust_id].items_in_hand.add(item_id)
        risk.add(cust_id, 1)
        clip.create(event="PICK", status="TEMP")

    elif e.type == "PUT_BACK_CONFIRMED":
        item_id = e.item_id
        ledger[cust_id].remove_from_possessed(item_id)
        ledger[cust_id].items_returned.add(item_id)
        item_store.update(item_id, status="RETURNED", ts=e.ts)
        risk.add(cust_id, -2)

    elif e.type in {"CONCEAL_POCKET","CONCEAL_PANTS","CONCEAL_HOODIE","CONCEAL_SHIRT","CONCEAL_BAG"}:
        item_id = e.item_id
        ledger[cust_id].items_in_hand.discard(item_id)
        ledger[cust_id].items_concealed.add(item_id)
        item_store.update(item_id, status="CONCEALED", disappearance_reason=e.conceal_type, ts=e.ts)
        risk.add(cust_id, 6)
        clip.create(event=e.type, status="KEEP")

    elif e.type == "COUNTER_PRESENTED":
        item_id = associate_counter_item(e, cust_id)
        if item_id:
            move_to_counter(cust_id, item_id, e.ts)

    elif e.type == "COUNTER_RECONCILE":
        mismatch = reconcile_counter(cust_id)
        if mismatch.missing_count > 0:
            risk.add(cust_id, 8)
            emit("COUNTER_MISMATCH", cust_id, missing_count=mismatch.missing_count, clips=mismatch.clip_ids)
            clip.create(event="COUNTER_MISMATCH", status="KEEP")
        else:
            risk.add(cust_id, -3)
            emit("COUNTER_RESOLVED", cust_id)

    elif e.type == "EXIT_CROSS":
        close_session(cust_id, e.ts)
```

```python
def close_session(cust_id, ts):
    st = session_store[cust_id]
    unresolved = (len(st.ledger.items_concealed) > 0) or st.mismatch_open or (st.unpaid_count > 0)
    if unresolved:
        emit("HIGH_RISK_EXIT", cust_id, risk=risk.current(cust_id))
        clip.mark_all_related(cust_id, status="KEEP", retention_days=30)
        st.state = "ALERT_CLOSED"
    else:
        emit("SESSION_CLEARED", cust_id, risk=risk.current(cust_id))
        clip.mark_temp_for_delete_pending(cust_id, retention_hours=24)
        st.state = "CLEARED"
```

## 13) Tunable Parameters (defaults)

- `N_PICK_WRIST_SHELF_FRAMES=5`
- `M_PICK_AWAY_FRAMES=6`
- `S_PUTBACK_STATIC_FRAMES=10`
- `K_CONCEAL_DISAPPEAR_FRAMES=8`
- `C_COUNTER_STABLE_FRAMES=12`
- `TH_GLOBAL_MATCH=0.72`
- `T_HANDOFF_SEC=10`
- `TH_CONCEAL_CONF=0.65`
- `TH_UNCERTAIN_LOW=0.45`
- `RISK_YELLOW=8`
- `RISK_RED=12`
- `TEMP_CLIP_RETENTION_HOURS=24`
- `KEEP_CLIP_RETENTION_DAYS=30`
