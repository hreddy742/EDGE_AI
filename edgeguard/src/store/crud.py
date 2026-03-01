import json
from datetime import datetime
import uuid

from sqlalchemy.orm import Session

from src.rules.theft_fsm import TheftSignal, TrackRiskPoint
from src.store.models import ClipRecord, CustomerTrackRecord, EventRecord, ItemTrackRecord, SignalRecord, TrackTimelineRecord


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def create_event(
    db: Session,
    event_id: str,
    camera_id: str,
    track_id: int,
    event_type: str,
    ts_start: datetime,
    ts_trigger: datetime,
    risk_score_at_trigger: float,
    short_explanation: str,
    snapshot_path: str | None,
    details: dict,
) -> EventRecord:
    record = EventRecord(
        event_id=event_id,
        camera_id=camera_id,
        track_id=track_id,
        event_type=event_type,
        ts_start=ts_start,
        ts_trigger=ts_trigger,
        ts=ts_trigger,
        risk_score_at_trigger=risk_score_at_trigger,
        short_explanation=short_explanation,
        snapshot_path=snapshot_path,
        details=json.dumps(details),
        confidence=risk_score_at_trigger,
    )
    db.add(record)
    _commit(db)
    db.refresh(record)
    return record


def create_signals(db: Session, camera_id: str, signals: list[TheftSignal], event_id: str | None = None) -> None:
    if not signals:
        return
    records = [
        SignalRecord(
            signal_id=str(uuid.uuid4()),
            event_id=event_id,
            camera_id=camera_id,
            track_id=signal.track_id,
            signal_type=signal.signal_type,
            ts=signal.ts,
            value=signal.value,
            details=json.dumps(signal.details),
        )
        for signal in signals
    ]
    db.bulk_save_objects(records)
    _commit(db)


def create_signals_batch(
    db: Session,
    camera_id: str,
    signals: list[TheftSignal],
    event_id: str | None = None,
) -> None:
    """Bulk-insert signals in a single commit (5–10× faster than one-by-one)."""
    create_signals(db, camera_id, signals, event_id)


def create_track_points(db: Session, points: list[TrackRiskPoint]) -> None:
    if not points:
        return
    records = [
        TrackTimelineRecord(
            camera_id=point.camera_id,
            track_id=point.track_id,
            ts=point.ts,
            risk_score=point.risk_score,
            state=point.state,
            centroid_x=point.centroid_x,
            centroid_y=point.centroid_y,
            velocity=point.velocity,
            details=json.dumps(point.details),
        )
        for point in points
    ]
    db.bulk_save_objects(records)
    _commit(db)


def create_track_points_batch(db: Session, points: list[TrackRiskPoint]) -> None:
    """Bulk-insert track timeline points in a single commit."""
    create_track_points(db, points)


def list_events(
    db: Session,
    camera_id: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
) -> list[EventRecord]:
    query = db.query(EventRecord)

    if camera_id:
        query = query.filter(EventRecord.camera_id == camera_id)
    if event_type:
        query = query.filter(EventRecord.event_type == event_type)
    if since:
        query = query.filter(EventRecord.ts_trigger >= since)
    if until:
        query = query.filter(EventRecord.ts_trigger <= until)

    return query.order_by(EventRecord.ts_trigger.desc()).limit(limit).all()


def get_event_by_event_id(db: Session, event_id: str) -> EventRecord | None:
    return db.query(EventRecord).filter(EventRecord.event_id == event_id).first()


def list_track_timeline(
    db: Session,
    track_id: int,
    camera_id: str | None = None,
    limit: int = 1000,
) -> list[TrackTimelineRecord]:
    query = db.query(TrackTimelineRecord).filter(TrackTimelineRecord.track_id == track_id)
    if camera_id:
        query = query.filter(TrackTimelineRecord.camera_id == camera_id)
    return query.order_by(TrackTimelineRecord.ts.asc()).limit(limit).all()


def list_signals_for_track(
    db: Session,
    track_id: int,
    camera_id: str | None = None,
    limit: int = 500,
) -> list[SignalRecord]:
    query = db.query(SignalRecord).filter(SignalRecord.track_id == track_id)
    if camera_id:
        query = query.filter(SignalRecord.camera_id == camera_id)
    return query.order_by(SignalRecord.ts.asc()).limit(limit).all()


def serialize_event(record: EventRecord) -> dict:
    details = {}
    if record.details:
        try:
            details = json.loads(record.details)
        except json.JSONDecodeError:
            details = {"raw": record.details}
    ts_fallback = getattr(record, "ts", None)
    ts_start = record.ts_start or ts_fallback
    ts_trigger = record.ts_trigger or ts_fallback
    return {
        "event_id": record.event_id,
        "camera_id": record.camera_id,
        "ts_start": ts_start,
        "ts_trigger": ts_trigger,
        "track_id": record.track_id,
        "event_type": record.event_type,
        "risk_score_at_trigger": record.risk_score_at_trigger if record.risk_score_at_trigger is not None else 0.0,
        "snapshot_path": record.snapshot_path,
        "short_explanation": record.short_explanation or "",
        "details": details,
    }


def serialize_signal(record: SignalRecord) -> dict:
    details = {}
    if record.details:
        try:
            details = json.loads(record.details)
        except json.JSONDecodeError:
            details = {"raw": record.details}
    return {
        "signal_id": record.signal_id,
        "event_id": record.event_id,
        "camera_id": record.camera_id,
        "track_id": record.track_id,
        "signal_type": record.signal_type,
        "ts": record.ts,
        "value": record.value,
        "details": details,
    }


def serialize_timeline_point(record: TrackTimelineRecord) -> dict:
    details = {}
    if record.details:
        try:
            details = json.loads(record.details)
        except json.JSONDecodeError:
            details = {"raw": record.details}
    return {
        "camera_id": record.camera_id,
        "track_id": record.track_id,
        "ts": record.ts,
        "risk_score": record.risk_score,
        "state": record.state,
        "centroid_x": record.centroid_x,
        "centroid_y": record.centroid_y,
        "velocity": record.velocity,
        "details": details,
    }


def list_customers(db: Session, limit: int = 500) -> list[CustomerTrackRecord]:
    return db.query(CustomerTrackRecord).order_by(CustomerTrackRecord.last_seen_time.desc()).limit(limit).all()


def get_customer(db: Session, global_customer_id: str) -> CustomerTrackRecord | None:
    return db.query(CustomerTrackRecord).filter(CustomerTrackRecord.global_customer_id == global_customer_id).first()


def list_items(db: Session, owner_customer_id: str | None = None, limit: int = 1000) -> list[ItemTrackRecord]:
    query = db.query(ItemTrackRecord)
    if owner_customer_id:
        query = query.filter(ItemTrackRecord.owner_customer_id == owner_customer_id)
    return query.order_by(ItemTrackRecord.last_status_change_time.desc()).limit(limit).all()


def create_clip(
    db: Session,
    clip_id: str,
    event_id: str,
    camera_id: str,
    track_id: int | None,
    ts_start: datetime,
    ts_end: datetime,
    status: str = "TEMP",
    processing_status: str = "PENDING",
    clip_path: str | None = None,
    retention_until: datetime | None = None,
) -> ClipRecord:
    record = ClipRecord(
        clip_id=clip_id,
        event_id=event_id,
        camera_id=camera_id,
        track_id=track_id,
        ts_start=ts_start,
        ts_end=ts_end,
        status=status,
        processing_status=processing_status,
        retention_until=retention_until,
        clip_path=clip_path,
    )
    db.add(record)
    _commit(db)
    db.refresh(record)
    return record


def update_clip_status(db: Session, clip_id: str, status: str, clip_path: str | None = None) -> ClipRecord | None:
    record = db.query(ClipRecord).filter(ClipRecord.clip_id == clip_id).first()
    if record is None:
        return None
    record.status = status
    if clip_path is not None:
        record.clip_path = clip_path
    _commit(db)
    db.refresh(record)
    return record


def update_clip_processing(
    db: Session,
    clip_id: str,
    processing_status: str,
    clip_path: str | None = None,
) -> ClipRecord | None:
    record = db.query(ClipRecord).filter(ClipRecord.clip_id == clip_id).first()
    if record is None:
        return None
    record.processing_status = processing_status
    if clip_path is not None:
        record.clip_path = clip_path
    _commit(db)
    db.refresh(record)
    return record


def update_clip_retention(
    db: Session,
    clip_id: str,
    status: str,
    retention_until: datetime | None,
) -> ClipRecord | None:
    record = db.query(ClipRecord).filter(ClipRecord.clip_id == clip_id).first()
    if record is None:
        return None
    record.status = status
    record.retention_until = retention_until
    _commit(db)
    db.refresh(record)
    return record


def get_clip(db: Session, clip_id: str) -> ClipRecord | None:
    return db.query(ClipRecord).filter(ClipRecord.clip_id == clip_id).first()


def list_clips(db: Session, event_id: str | None = None, limit: int = 500) -> list[ClipRecord]:
    query = db.query(ClipRecord)
    if event_id:
        query = query.filter(ClipRecord.event_id == event_id)
    return query.order_by(ClipRecord.ts_start.desc()).limit(limit).all()


def _parse_json(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def serialize_customer(record: CustomerTrackRecord) -> dict:
    return {
        "global_customer_id": record.global_customer_id,
        "current_camera_id": record.current_camera_id,
        "current_zone": record.current_zone,
        "entry_time": record.entry_time,
        "last_seen_time": record.last_seen_time,
        "risk_score_current": record.risk_score_current,
        "basket_state": _parse_json(record.basket_state, {}),
        "evidence_links": _parse_json(record.evidence_links, []),
    }


def serialize_item(record: ItemTrackRecord) -> dict:
    return {
        "global_item_id": record.global_item_id,
        "item_class": record.item_class,
        "current_status": record.current_status,
        "owner_customer_id": record.owner_customer_id,
        "last_seen_camera_id": record.last_seen_camera_id,
        "last_seen_bbox": record.last_seen_bbox,
        "first_pick_time": record.first_pick_time,
        "last_status_change_time": record.last_status_change_time,
        "confidence": record.confidence,
        "disappearance_reason": record.disappearance_reason,
        "evidence_clips": _parse_json(record.evidence_clips, []),
    }


def serialize_clip(record: ClipRecord) -> dict:
    return {
        "clip_id": record.clip_id,
        "event_id": record.event_id,
        "camera_id": record.camera_id,
        "track_id": record.track_id,
        "ts_start": record.ts_start,
        "ts_end": record.ts_end,
        "status": record.status,
        "processing_status": record.processing_status,
        "retention_until": record.retention_until,
        "clip_path": record.clip_path,
    }


def list_alert_sessions(db: Session, min_risk: float = 8.0, limit: int = 200) -> list[CustomerTrackRecord]:
    return (
        db.query(CustomerTrackRecord)
        .filter(CustomerTrackRecord.risk_score_current >= min_risk)
        .order_by(CustomerTrackRecord.risk_score_current.desc())
        .limit(limit)
        .all()
    )


def list_clips_for_customer(db: Session, customer_id: str, limit: int = 500) -> list[ClipRecord]:
    event_ids: list[str] = []
    events = db.query(EventRecord).order_by(EventRecord.ts_trigger.desc()).limit(5000).all()
    for row in events:
        details = _parse_json(row.details, {})
        if details.get("customer_id") == customer_id:
            event_ids.append(row.event_id)
            continue
        if customer_id.startswith(f"{row.camera_id}:") and customer_id.endswith(str(row.track_id)):
            event_ids.append(row.event_id)
    if not event_ids:
        return []
    return (
        db.query(ClipRecord)
        .filter(ClipRecord.event_id.in_(event_ids))
        .order_by(ClipRecord.ts_start.desc())
        .limit(limit)
        .all()
    )


def upsert_customer(
    db: Session,
    global_customer_id: str,
    current_camera_id: str | None = None,
    current_zone: str | None = None,
    entry_time: datetime | None = None,
    last_seen_time: datetime | None = None,
    risk_score_current: float | None = None,
    basket_state: dict | None = None,
    evidence_links: list[str] | None = None,
) -> CustomerTrackRecord:
    record = db.query(CustomerTrackRecord).filter(CustomerTrackRecord.global_customer_id == global_customer_id).first()
    if record is None:
        record = CustomerTrackRecord(
            global_customer_id=global_customer_id,
            current_camera_id=current_camera_id,
            current_zone=current_zone,
            entry_time=entry_time,
            last_seen_time=last_seen_time,
            risk_score_current=risk_score_current or 0.0,
            basket_state=json.dumps(basket_state or {}),
            evidence_links=json.dumps(evidence_links or []),
        )
        db.add(record)
        _commit(db)
        db.refresh(record)
        return record

    if current_camera_id is not None:
        record.current_camera_id = current_camera_id
    if current_zone is not None:
        record.current_zone = current_zone
    if entry_time is not None and record.entry_time is None:
        record.entry_time = entry_time
    if last_seen_time is not None:
        record.last_seen_time = last_seen_time
    if risk_score_current is not None:
        record.risk_score_current = risk_score_current
    if basket_state is not None:
        record.basket_state = json.dumps(basket_state)
    if evidence_links is not None:
        record.evidence_links = json.dumps(evidence_links)
    _commit(db)
    db.refresh(record)
    return record


def upsert_item(
    db: Session,
    global_item_id: str,
    item_class: str = "unknown",
    current_status: str = "ON_SHELF",
    owner_customer_id: str | None = None,
    last_seen_camera_id: str | None = None,
    last_seen_bbox: str | None = None,
    first_pick_time: datetime | None = None,
    last_status_change_time: datetime | None = None,
    confidence: float = 0.0,
    disappearance_reason: str | None = None,
    evidence_clips: list[str] | None = None,
) -> ItemTrackRecord:
    record = db.query(ItemTrackRecord).filter(ItemTrackRecord.global_item_id == global_item_id).first()
    if record is None:
        record = ItemTrackRecord(
            global_item_id=global_item_id,
            item_class=item_class,
            current_status=current_status,
            owner_customer_id=owner_customer_id,
            last_seen_camera_id=last_seen_camera_id,
            last_seen_bbox=last_seen_bbox,
            first_pick_time=first_pick_time,
            last_status_change_time=last_status_change_time,
            confidence=confidence,
            disappearance_reason=disappearance_reason,
            evidence_clips=json.dumps(evidence_clips or []),
        )
        db.add(record)
        _commit(db)
        db.refresh(record)
        return record

    record.item_class = item_class
    record.current_status = current_status
    record.owner_customer_id = owner_customer_id
    record.last_seen_camera_id = last_seen_camera_id
    record.last_seen_bbox = last_seen_bbox
    if first_pick_time is not None and record.first_pick_time is None:
        record.first_pick_time = first_pick_time
    record.last_status_change_time = last_status_change_time
    record.confidence = confidence
    record.disappearance_reason = disappearance_reason
    if evidence_clips is not None:
        record.evidence_clips = json.dumps(evidence_clips)
    _commit(db)
    db.refresh(record)
    return record
