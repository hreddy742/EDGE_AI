from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.store.db import Base


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True)
    track_id: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    ts_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    ts_trigger: Mapped[datetime] = mapped_column(DateTime, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    risk_score_at_trigger: Mapped[float] = mapped_column(Float)
    short_explanation: Mapped[str] = mapped_column(String(512))
    snapshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    details: Mapped[str] = mapped_column(Text, default="{}")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


class SignalRecord(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    signal_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True)
    track_id: Mapped[int] = mapped_column(Integer, index=True)
    signal_type: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    value: Mapped[float] = mapped_column(Float)
    details: Mapped[str] = mapped_column(Text, default="{}")


class TrackTimelineRecord(Base):
    __tablename__ = "track_timeline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True)
    track_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    risk_score: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(64))
    centroid_x: Mapped[float] = mapped_column(Float)
    centroid_y: Mapped[float] = mapped_column(Float)
    velocity: Mapped[float] = mapped_column(Float)
    details: Mapped[str] = mapped_column(Text, default="{}")


class CustomerTrackRecord(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    global_customer_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    current_camera_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    current_zone: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_seen_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    risk_score_current: Mapped[float] = mapped_column(Float, default=0.0)
    basket_state: Mapped[str] = mapped_column(Text, default="{}")
    evidence_links: Mapped[str] = mapped_column(Text, default="[]")


class ItemTrackRecord(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    global_item_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    item_class: Mapped[str] = mapped_column(String(64), default="unknown")
    current_status: Mapped[str] = mapped_column(String(32), index=True, default="ON_SHELF")
    owner_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_seen_camera_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_seen_bbox: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_pick_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status_change_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    disappearance_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence_clips: Mapped[str] = mapped_column(Text, default="[]")


class ClipRecord(Base):
    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    clip_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ts_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    ts_end: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(32), default="TEMP", index=True)
    processing_status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    clip_path: Mapped[str | None] = mapped_column(String(512), nullable=True)


class CustomerSessionRecord(Base):
    __tablename__ = "customer_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    global_customer_id: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True, default="ACTIVE")
    opened_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    close_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
