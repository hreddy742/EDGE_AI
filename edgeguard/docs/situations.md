# EdgeGuard Real-World Situations

## A) Normal Situations

1. Entry -> browse -> pick 1 item -> place on counter -> pay -> exit.
2. Entry -> pick multiple items -> all remain visible -> counter presentation complete -> pay -> exit.
3. Entry -> pick item -> put back same shelf -> no remaining items -> exit.
4. Entry -> pick 6 items -> put back 2 -> present/pay 4 -> exit.
5. Entry -> move across aisle cameras with same customer identity -> normal checkout -> exit.
6. Item temporarily occluded in aisle, reappears in hand/cart, then normally presented at counter.
7. Customer waits in checkout line with items in hand/cart, no concealment, then pays.
8. Customer carries personal bag but never hides store item; normal purchase flow.
9. Customer touches shelf items repeatedly but leaves empty-handed.
10. Customer returns after counter to shelf area, puts back one item, then re-checkout.

## B) Theft-Risk Situations

1. Pick -> conceal in pocket -> skip counter presentation -> exit.
2. Pick -> conceal in hoodie/jacket -> present only visible subset at counter -> exit.
3. Pick -> conceal in shirt/torso area -> no reveal at counter -> exit.
4. Pick -> place into personal bag -> partial presentation at counter -> exit.
5. Multiple quick picks + concealment sequence in short time window.
6. Pick -> move to blind area/occlusion -> item never reappears -> mismatch at counter.
7. Pick in aisle camera A, conceal in aisle camera B, exit through entry/exit camera.
8. Counter mismatch unresolved -> immediate exit crossing.
9. Stash behavior: item hidden in unusual zone (not shelf/counter/cart) then exit.
10. Walkout from aisle directly to exit without counter interaction.

## C) Checkout/Counter Situations

1. All possessed items are presented at counter quickly (no mismatch).
2. Only subset presented initially, then customer presents remaining items (resolved mismatch).
3. Concealed item revealed at counter and presented; risk should be downgraded.
4. Concealed item stays concealed at checkout; mismatch remains.
5. Ambiguous presented item cannot be matched to existing ItemID (count used as fallback).
6. Customer removes item from cart to counter in multiple batches.
7. Two customers at counter simultaneously; association uncertainty increases.
8. Optional self-checkout: bagging action without scanner action (non-scan risk).
9. POS mode: paid count < possessed count (checkout mismatch).
10. POS mode: paid count == possessed count (paid verified).

## D) Multi-Camera Continuity Situations

1. Entry camera creates session, aisle camera picks, counter camera reconciles, exit camera closes.
2. Customer disappears in one camera and reappears in adjacent camera within transition window.
3. Overlap handoff between two cameras with concurrent local tracks.
4. Similar-looking customers crossing in opposite directions near handoff boundary.
5. Temporary ID fragmentation then merge into one GlobalCustomerID.
6. Group enters together, splits into different aisles, reunites at counter.
7. Camera drop/reconnect while customer session is active.
8. Counter event arrives before aisle late event (out-of-order event handling).

## E) Ambiguity/Uncertainty Situations

1. Heavy occlusion by another person near shelf.
2. Shelf clutter causes unstable item candidate boxes.
3. Person tracker ID switch in crowded aisle.
4. Item vanishes due to motion blur, not concealment.
5. Customer handoff: one customer passes item to another.
6. Parent/child close interaction with shared cart.
7. Bag region false positives from coat folds.
8. Counter reflections causing false item detections.
9. Exit zone crossing ambiguity at boundary.
10. Short video gaps due to RTSP jitter.

## F) Return/Put-Back Situations

1. Put back item to original shelf zone.
2. Put back item in different shelf zone.
3. Put back attempt fails (item dropped, not static on shelf).
4. Item placed on random surface (not shelf/counter), should become LOST_UNCERTAIN.
5. Item returned after temporary concealment and reappearance.
6. Multiple returns after multi-pick (counts must reduce exactly).

## G) Clip Retention/Deletion Situations

1. Pick-only TEMP clips for a customer later cleared at session close -> mark DELETE_PENDING.
2. Conceal clip generated -> KEEP immediately.
3. Counter mismatch clip generated -> KEEP immediately.
4. Exit alert clip generated -> KEEP immediately.
5. Mismatch later resolved at counter, but keep clips until session close decision.
6. Cleared session with no unresolved conceal/mismatch -> TEMP clips become DELETE_PENDING.
7. Alert session unresolved at exit -> all related evidence remains KEEP.
8. Background cleanup deletes files for expired DELETE_PENDING -> status DELETED in DB.
9. Manual reviewer flags clip -> force KEEP until review complete.
10. Legal/retention override extends retention_until.
