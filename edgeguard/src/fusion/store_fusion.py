from dataclasses import dataclass
from datetime import datetime

from src.fusion.global_identity import GlobalIdentityResolver


@dataclass
class FusionEvent:
    camera_id: str
    timestamp: datetime
    local_person_id: str
    event_type: str
    embedding: list[float] | None = None
    local_item_id: str | None = None
    item_id: str | None = None
    confidence: float = 0.0
    details: dict | None = None


class StoreFusionEngine:
    def __init__(self) -> None:
        self.identity = GlobalIdentityResolver()
        self._buffer: list[dict] = []

    def ingest_perception_event(self, event: dict) -> None:
        self._buffer.append(event)

    def flush_ready_events(self) -> list[dict]:
        out = list(self._buffer)
        self._buffer.clear()
        return out
