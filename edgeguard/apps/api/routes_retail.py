import asyncio
from datetime import datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.store import crud
from src.store.db import get_db, get_session_local

router = APIRouter(prefix="/retail", tags=["retail"])

@router.get("/customers")
def list_customers(
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    rows = crud.list_customers(db=db, limit=limit)
    return {"items": [crud.serialize_customer(row) for row in rows]}


@router.get("/customers/stream")
async def stream_customers(
    limit: int = Query(default=200, ge=1, le=2000),
    heartbeat_seconds: float = Query(default=3.0, ge=0.5, le=30.0),
) -> StreamingResponse:
    session_local = get_session_local()

    async def customer_generator():
        last_fingerprint = ""
        while True:
            db = session_local()
            sent_now = False
            try:
                rows = crud.list_customers(db=db, limit=limit)
                payload = [crud.serialize_customer(row) for row in rows]
                fingerprint = json.dumps(payload, default=str, separators=(",", ":"))
                if fingerprint != last_fingerprint:
                    last_fingerprint = fingerprint
                    yield f"event: customers\ndata: {fingerprint}\n\n"
                    sent_now = True
                if not sent_now:
                    yield f"event: heartbeat\ndata: {json.dumps({'ts': datetime.utcnow().isoformat()})}\n\n"
            finally:
                db.close()
            await asyncio.sleep(0.2 if sent_now else heartbeat_seconds)

    return StreamingResponse(
        customer_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/customers/{customer_id}")
def get_customer(customer_id: str, db: Session = Depends(get_db)) -> dict:
    row = crud.get_customer(db=db, global_customer_id=customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return crud.serialize_customer(row)


@router.get("/items")
def list_items(
    owner_customer_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    rows = crud.list_items(db=db, owner_customer_id=owner_customer_id, limit=limit)
    return {"items": [crud.serialize_item(row) for row in rows]}


@router.get("/events")
def list_retail_events(
    camera_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    rows = crud.list_events(db=db, camera_id=camera_id, event_type=event_type, limit=limit)
    return {"items": [crud.serialize_event(row) for row in rows]}


@router.get("/clips/{clip_id}")
def get_clip(clip_id: str, db: Session = Depends(get_db)) -> dict:
    row = crud.get_clip(db=db, clip_id=clip_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return crud.serialize_clip(row)


@router.get("/clips")
def list_clips(
    event_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    rows = crud.list_clips(db=db, event_id=event_id, limit=limit)
    return {"items": [crud.serialize_clip(row) for row in rows]}


@router.get("/customers/{customer_id}/clips")
def list_customer_clips(
    customer_id: str,
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    rows = crud.list_clips_for_customer(db=db, customer_id=customer_id, limit=limit)
    return {"items": [crud.serialize_clip(row) for row in rows]}
