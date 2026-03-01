from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PersonTrackSchema(BaseModel):
    track_id: int
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    conf: float
    centroid: list[float] = Field(..., min_length=2, max_length=2)
    velocity: float | None = None
    last_seen_ts: datetime


class PoseKeypointsSchema(BaseModel):
    keypoints: list[list[float]] = Field(default_factory=list)
    left_wrist: list[float] | None = None
    right_wrist: list[float] | None = None
    hip_center: list[float] | None = None
    hand_to_hip_distance: float | None = None
    hand_speed: float | None = None
    available: bool


class TheftSignalSchema(BaseModel):
    signal_type: str
    track_id: int
    ts: datetime
    value: float
    details: dict[str, Any] = Field(default_factory=dict)


class TheftEventSchema(BaseModel):
    event_id: str
    camera_id: str
    ts_start: datetime
    ts_trigger: datetime
    track_id: int | None = None
    event_type: str
    risk_score_at_trigger: float
    snapshot_path: str | None = None
    short_explanation: str
    details: dict[str, Any] = Field(default_factory=dict)


class TrackTimelinePointSchema(BaseModel):
    camera_id: str
    track_id: int
    ts: datetime
    risk_score: float
    state: str
    centroid_x: float
    centroid_y: float
    velocity: float
    details: dict[str, Any] = Field(default_factory=dict)


class TrackTimelineResponse(BaseModel):
    track_id: int
    camera_id: str | None = None
    points: list[TrackTimelinePointSchema]
    signals: list[TheftSignalSchema]


class HealthResponse(BaseModel):
    status: str
    camera_id: str
    source_type: str
    mode: str
    cameras: list[str] = Field(default_factory=list)
