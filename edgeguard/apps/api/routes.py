import asyncio
from datetime import datetime
import json
from fractions import Fraction
from typing import Any

import cv2
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.schemas import HealthResponse, TheftEventSchema, TrackTimelineResponse
from src.core.config import get_settings
from src.pipeline.manager import get_pipeline_manager
from src.store import crud
from src.store.db import get_db, get_session_local

router = APIRouter()
_pcs: set[RTCPeerConnection] = set()


class WebRTCOffer(BaseModel):
    sdp: str
    type: str
    camera_id: str | None = None


class RunnerVideoTrack(VideoStreamTrack):
    """WebRTC video track that serves the latest annotated frame from a pipeline runner."""

    def __init__(self, runner) -> None:
        super().__init__()
        self.runner = runner
        self._last_frame = np.zeros((360, 640, 3), dtype=np.uint8)
        self._target_fps = 24
        self._time_base = Fraction(1, 90000)
        self._pts = 0

    async def recv(self) -> VideoFrame:
        await asyncio.sleep(1 / self._target_fps)  # smoother browser playback
        frame_bytes = self.runner.get_latest_frame_bytes()
        if frame_bytes:
            arr = np.frombuffer(frame_bytes, dtype=np.uint8)
            decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if decoded is not None:
                self._last_frame = decoded
        self._pts += int(90000 / self._target_fps)
        vf = VideoFrame.from_ndarray(self._last_frame, format="bgr24")
        vf.pts = self._pts
        vf.time_base = self._time_base
        return vf


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    manager = get_pipeline_manager(settings)
    cameras = manager.list_cameras()
    return HealthResponse(
        status="ok",
        camera_id=(cameras[0] if cameras else settings.camera_id),
        source_type=settings.video_source_type,
        mode=settings.mode,
        cameras=cameras,
    )


@router.post("/webrtc/offer")
async def webrtc_offer(offer: WebRTCOffer) -> dict[str, Any]:
    settings = get_settings()
    manager = get_pipeline_manager(settings)
    runner = manager.get_runner(offer.camera_id)

    pc = RTCPeerConnection()
    _pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_state_change() -> None:
        if pc.connectionState in {"failed", "closed", "disconnected"}:
            await pc.close()
            _pcs.discard(pc)

    pc.addTrack(RunnerVideoTrack(runner))
    await pc.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


@router.get("/events", response_model=list[TheftEventSchema])
def list_events(
    camera_id: str | None = None,
    event_type: str | None = None,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[TheftEventSchema]:
    records = crud.list_events(
        db=db,
        camera_id=camera_id,
        event_type=event_type,
        since=since,
        until=until,
        limit=limit,
    )
    return [TheftEventSchema(**crud.serialize_event(record)) for record in records]


@router.get("/events/{event_id}", response_model=TheftEventSchema)
def get_event(event_id: str, db: Session = Depends(get_db)) -> TheftEventSchema:
    record = crud.get_event_by_event_id(db=db, event_id=event_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return TheftEventSchema(**crud.serialize_event(record))


@router.get("/tracks/{track_id}/timeline", response_model=TrackTimelineResponse)
def track_timeline(
    track_id: int,
    camera_id: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> TrackTimelineResponse:
    points = crud.list_track_timeline(db=db, track_id=track_id, camera_id=camera_id, limit=limit)
    signals = crud.list_signals_for_track(db=db, track_id=track_id, camera_id=camera_id, limit=limit)
    return TrackTimelineResponse(
        track_id=track_id,
        camera_id=camera_id,
        points=[crud.serialize_timeline_point(point) for point in points],
        signals=[crud.serialize_signal(signal) for signal in signals],
    )


@router.get("/latest_frame")
def latest_frame(camera_id: str | None = None) -> Response:
    settings = get_settings()
    runner = get_pipeline_manager(settings).get_runner(camera_id)
    frame_bytes = runner.get_latest_frame_bytes()
    if frame_bytes is None:
        raise HTTPException(status_code=404, detail="No frame available yet")
    return Response(content=frame_bytes, media_type="image/jpeg")


@router.get("/live/events/stream")
async def stream_events(
    camera_id: str | None = None,
    event_type: str | None = None,
    heartbeat_seconds: float = Query(default=10.0, ge=1.0, le=60.0),
) -> StreamingResponse:
    session_local = get_session_local()

    async def event_generator():
        last_sent: set[str] = set()
        while True:
            sent_now = False
            db = session_local()
            try:
                rows = crud.list_events(db=db, camera_id=camera_id, event_type=event_type, limit=200)
                rows.reverse()
                for row in rows:
                    payload = crud.serialize_event(row)
                    event_id = payload.get("event_id")
                    if not event_id or event_id in last_sent:
                        continue
                    last_sent.add(event_id)
                    yield f"event: theft_event\ndata: {json.dumps(payload, default=str)}\n\n"
                    sent_now = True
                if len(last_sent) > 2000:
                    last_sent = set(list(last_sent)[-1000:])
                if not sent_now:
                    yield f"event: heartbeat\ndata: {json.dumps({'ts': datetime.utcnow().isoformat()})}\n\n"
            finally:
                db.close()
            await asyncio.sleep(heartbeat_seconds if not sent_now else 0.2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
