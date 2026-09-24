# EdgeGuard Module Plan and Interfaces

## 1) Config and Store Setup

## 1.1 `src/core/store_config.py`
```python
from dataclasses import dataclass
from typing import Literal

CameraRole = Literal["ENTRY_EXIT", "AISLE", "COUNTER"]

@dataclass
class CameraConfig:
    camera_id: str
    role: CameraRole
    rtsp_url: str
    zones: dict[str, list[tuple[int, int]]]

@dataclass
class StoreConfig:
    store_id: str
    cameras: list[CameraConfig]
    adjacency: dict[str, list[str]]

def load_store_config(path: str) -> StoreConfig: ...
def validate_store_config(cfg: StoreConfig) -> None: ...
```

## 1.2 `src/core/thresholds.py`
```python
from dataclasses import dataclass

@dataclass
class Thresholds:
    n_pick_wrist_shelf_frames: int
    m_pick_away_frames: int
    s_putback_static_frames: int
    k_conceal_missing_frames: int
    c_counter_stable_frames: int
    th_global_match: float
    risk_green_max: float
    risk_red_min: float

def load_thresholds() -> Thresholds: ...
```

## 2) Per-Camera Worker

## 2.1 `src/pipeline/camera_worker.py`
```python
from dataclasses import dataclass
from datetime import datetime
import numpy as np

@dataclass
class FrameEvent:
    camera_id: str
    role: str
    ts: datetime
    frame: np.ndarray

class CameraWorker:
    def __init__(self, camera_cfg, out_queue): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

## 2.2 `src/pipeline/per_camera_processor.py`
```python
class PerCameraProcessor:
    def __init__(self, camera_cfg, models, thresholds): ...
    def process_frame(self, frame_event: FrameEvent) -> list[dict]:
        """Returns normalized perception events/signals with local IDs."""
```

## 3) Vision Modules

## 3.1 `src/vision/person_detector.py`
```python
class PersonDetector:
    def detect(self, frame) -> list[dict]:
        """bbox, conf"""
```

## 3.2 `src/vision/person_tracker.py`
```python
class PersonTracker:
    def update(self, frame, detections, ts) -> list[dict]:
        """local_person_track_id, bbox, centroid, velocity"""
```

## 3.3 `src/vision/reid.py`
```python
class ReIDEmbedder:
    def embed(self, person_crop) -> list[float] | None: ...
    def cosine_similarity(self, a: list[float], b: list[float]) -> float: ...
```

## 3.4 `src/vision/pose.py`
```python
class PoseEstimator:
    def estimate(self, frame, tracks, ts) -> dict[int, dict]:
        """wrists, hips, torso, confidences"""
```

## 3.5 `src/vision/item_detector.py`
```python
class ItemDetector:
    def detect_candidates(self, frame, role: str, zones: dict) -> list[dict]:
        """item candidates for aisle/counter reasoning"""
```

## 3.6 `src/vision/item_tracker.py`
```python
class ItemTracker:
    def update(self, item_candidates, ts) -> list[dict]:
        """local item tracks with bbox/confidence"""
```

## 4) Fusion and Identity

## 4.1 `src/fusion/global_identity.py`
```python
class GlobalIdentityResolver:
    def resolve_customer_id(
        self,
        camera_id: str,
        local_track_id: int,
        embedding: list[float] | None,
        ts,
        bbox_size: tuple[float, float],
    ) -> str:
        """returns GlobalCustomerID"""
```

## 4.2 `src/fusion/store_fusion.py`
```python
class StoreFusionEngine:
    def ingest_perception_event(self, event: dict) -> None: ...
    def flush_ready_events(self) -> list[dict]: ...
```

## 5) Rules and Ledger

## 5.1 `src/rules/association.py`
```python
def associate_hand_item(customer_pose: dict, item_bbox: tuple[float,float,float,float]) -> float: ...
def is_pick_confirmed(context: dict, thresholds) -> bool: ...
def is_putback_confirmed(context: dict, thresholds) -> bool: ...
def detect_conceal_type(context: dict, thresholds) -> str | None: ...
```

## 5.2 `src/rules/theft_state_machine.py`
```python
class BasketLedger:
    items_in_hand: set[str]
    items_in_cart: set[str]
    items_concealed: set[str]
    items_on_counter: set[str]
    items_returned: set[str]
    items_paid: set[str]

class TheftStateMachine:
    def apply(self, customer_id: str, signal_type: str, item_id: str | None, ts) -> list[StateEvent]: ...
    def get_basket(self, customer_id: str) -> BasketLedger: ...
```

## 5.3 `src/rules/reconciliation.py`
```python
from dataclasses import dataclass

@dataclass
class ReconcileResult:
    missing_count: int
    missing_item_ids: list[str]
    resolved: bool

def reconcile_counter(ledger: BasketLedger, presented_unknown_count: int = 0) -> ReconcileResult: ...
def reconcile_pos(ledger: BasketLedger, paid_count: int | None, paid_item_ids: list[str] | None) -> ReconcileResult: ...
```

## 5.4 `src/rules/risk.py`
```python
class RiskEngine:
    def apply_delta(self, customer_id: str, delta: float, reason: str, now) -> float: ...
    def decay(self, customer_id: str, now) -> float: ...
    def band(self, score: float) -> str: ...
```

## 6) Evidence and Retention

## 6.1 `src/evidence/clip_writer.py`
```python
class RollingFrameBuffer:
    def append(self, ts, frame) -> None: ...
    def slice(self, start_ts, end_ts) -> list: ...

class ClipWriter:
    def write_event_clip(self, camera_id, event_id, ts_start, ts_end, buffer, prebuffer_sec, postbuffer_sec, fps) -> str | None: ...
```

## 6.2 `src/evidence/retention.py`
```python
def classify_clip_initial_status(event_type: str) -> str: ...
def apply_session_close_policy(session, clips, now) -> None: ...
def run_cleanup(now) -> int:
    """Delete expired DELETE_PENDING clip files; mark DELETED."""
```

## 7) Session Manager

## 7.1 `src/session/manager.py`
```python
class SessionManager:
    def open_or_get_session(self, customer_id: str, ts) -> str: ...
    def update_customer_presence(self, customer_id: str, camera_id: str, ts) -> None: ...
    def close_session(self, customer_id: str, reason: str, ts) -> dict: ...
```

## 8) Store Persistence

## 8.1 `src/store/models.py`
Tables:
- `customer_sessions`
- `customer_tracks`
- `item_tracks`
- `events`
- `clips`
- `track_timeline`
- `signals`

## 8.2 `src/store/crud.py`
```python
def upsert_customer(...): ...
def upsert_item(...): ...
def create_event(...): ...
def create_clip(...): ...
def update_clip_status(...): ...
def list_alert_sessions(...): ...
def list_clips_for_customer(customer_id: str): ...
```

## 9) API Endpoints

## 9.1 `apps/api/routes_config.py`
- `POST /config/cameras`
- `POST /config/cameras/{camera_id}/zones`
- `GET /config/cameras`

## 9.2 `apps/api/routes_monitor.py`
- `GET /health`
- `GET /retail/customers`
- `GET /retail/customers/{customer_id}`
- `GET /retail/events`
- `GET /retail/clips`
- `GET /retail/clips/{clip_id}`

## 9.3 `apps/api/routes_reconcile.py`
- `POST /retail/counter/reconcile`
- `POST /retail/pos/reconcile` (optional mode)
- `POST /retail/session/{customer_id}/close`

## 10) UI Modules

## 10.1 `apps/ui/pages/camera_setup.py`
- RTSP paste, role assignment, zone drawing.

## 10.2 `apps/ui/pages/live_monitor.py`
- per-customer cards:
  - `GlobalCustomerID`
  - risk band
  - counts (hand/concealed/counter/unpaid)
  - missing count
  - evidence clips

## 10.3 `apps/ui/pages/review_queue.py`
- unresolved alert sessions
- clip playback and reviewer decision
- retention override actions

## 11) Orchestration Entry Points

## 11.1 `src/pipeline/manager.py`
```python
class PipelineManager:
    def start_all(self) -> None: ...
    def stop_all(self) -> None: ...
    def get_runner(self, camera_id: str | None = None): ...
```

## 11.2 `apps/api/main.py`
- startup:
  - init DB
  - load config
  - start workers + fusion engine
- shutdown:
  - stop workers
  - flush pending clips/events
