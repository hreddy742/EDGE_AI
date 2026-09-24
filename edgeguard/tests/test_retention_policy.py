from datetime import datetime, timedelta

from src.evidence.retention import apply_session_close_policy


def test_apply_session_close_policy_cleared_session_marks_temp_for_deletion() -> None:
    now = datetime(2026, 2, 28, 12, 0, 0)
    clips = [
        {"clip_id": "c1", "status": "TEMP", "event_type": "PICK", "retention_until": None},
        {"clip_id": "c2", "status": "KEEP", "event_type": "CONCEAL_POCKET", "retention_until": None},
    ]
    apply_session_close_policy(session={"alert": False}, clips=clips, now=now)

    assert clips[0]["status"] == "DELETE_PENDING"
    assert clips[0]["retention_until"] == now + timedelta(hours=24)
    assert clips[1]["status"] == "KEEP"
    assert clips[1]["retention_until"] == now + timedelta(days=30)


def test_apply_session_close_policy_alert_session_keeps_all() -> None:
    now = datetime(2026, 2, 28, 12, 0, 0)
    clips = [
        {"clip_id": "c1", "status": "TEMP", "event_type": "PICK", "retention_until": None},
        {"clip_id": "c2", "status": "DELETE_PENDING", "event_type": "PICK", "retention_until": None},
    ]
    apply_session_close_policy(session={"alert": True}, clips=clips, now=now)

    assert clips[0]["status"] == "KEEP"
    assert clips[0]["retention_until"] == now + timedelta(days=30)
    assert clips[1]["status"] == "KEEP"
    assert clips[1]["retention_until"] == now + timedelta(days=30)
