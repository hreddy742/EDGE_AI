import os
from datetime import datetime, timedelta
from pathlib import Path

from src.store import crud
from src.store.db import get_session_local


def classify_clip_initial_status(event_type: str) -> str:
    if event_type in {"CONCEAL_POCKET", "CONCEAL_BAG", "CONCEAL_HOODIE", "COUNTER_MISMATCH", "HIGH_RISK_EXIT", "EXIT_ALERT"}:
        return "KEEP"
    return "TEMP"


def _coerce_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def apply_session_close_policy(session: dict, clips: list[dict], now: datetime) -> None:
    alert_session = bool(
        session.get("alert")
        or session.get("has_alert")
        or session.get("unresolved_alert")
        or session.get("exit_alert")
    )
    keep_days = int(os.getenv("KEEP_CLIP_RETENTION_DAYS", "30"))
    temp_hours = int(os.getenv("TEMP_CLIP_RETENTION_HOURS", "24"))
    keep_until = now + timedelta(days=keep_days)
    delete_until = now + timedelta(hours=temp_hours)

    for clip in clips:
        status = str(clip.get("status") or "TEMP")
        if status == "DELETED":
            continue

        event_type = str(clip.get("event_type") or "")
        is_critical = classify_clip_initial_status(event_type) == "KEEP"
        current_retention = _coerce_dt(clip.get("retention_until"))

        if alert_session:
            clip["status"] = "KEEP"
            clip["retention_until"] = max([d for d in [current_retention, keep_until] if d is not None], default=keep_until)
            continue

        # Cleared session: keep critical clips, schedule temp clips for deletion.
        if status == "KEEP" or is_critical:
            clip["status"] = "KEEP"
            clip["retention_until"] = max([d for d in [current_retention, keep_until] if d is not None], default=keep_until)
        elif status in {"TEMP", "DELETE_PENDING"}:
            clip["status"] = "DELETE_PENDING"
            clip["retention_until"] = current_retention or delete_until


def run_cleanup(now: datetime) -> int:
    deleted = 0
    session_local = get_session_local()
    db = session_local()
    try:
        candidates = crud.list_clips(db=db, limit=5000)
        for clip in candidates:
            data = crud.serialize_clip(clip)
            if data["status"] != "DELETE_PENDING":
                continue
            retention_until = data.get("retention_until")
            if retention_until is None:
                retention_until = data.get("ts_end")
                if retention_until is not None:
                    retention_until = retention_until + timedelta(hours=24)
            if retention_until is None or now <= retention_until:
                continue
            clip_path = data.get("clip_path")
            if clip_path:
                path = Path(clip_path)
                if path.exists():
                    path.unlink(missing_ok=True)
            crud.update_clip_status(db=db, clip_id=data["clip_id"], status="DELETED", clip_path=clip_path)
            deleted += 1
    finally:
        db.close()
    return deleted
