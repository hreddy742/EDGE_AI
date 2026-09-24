# EdgeGuard Theft Risk MVP

EdgeGuard is a local-first theft-risk video analytics MVP for retail footage.
It does **temporal risk analysis** (tracking + pose + FSM + risk scoring), not single-frame theft classification.

## What It Does
- Video ingestion from MP4 or RTSP
- Person detection with YOLOv8
- Person tracking with ByteTrack (`ultralytics.track`) and IOU fallback
- Pose keypoints (wrists/hips) with YOLOv8 pose
- Per-track theft-risk FSM and weighted signal scoring
- Risk events persisted to SQLite with snapshots and short clips
- FastAPI APIs for events, latest frame, and track risk timelines
- Streamlit dashboard with live stream, events, and per-track risk chart
- Multi-camera worker manager (one runner per configured camera)
- Optional webhook alert delivery on every emitted risk event

## Event Philosophy
Events are **risk alerts**, not certainty labels.
Event types:
- `POSSIBLE_CONCEALMENT`
- `HIGH_RISK_EXIT`
- `SELF_CHECKOUT_NONSCAN`

## Project Layout
```text
edgeguard/
  apps/
    api/
    ui/
  config/
    zones.sample.json
  docs/
    logic.md
    zones.md
  scripts/
    run_video.py
  src/
    core/
    video/
    vision/
    rules/
    store/
    pipeline/
  tests/
```

## Install
```powershell
cd edgeguard
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

## Configure
Edit `.env`:
- `VIDEO_SOURCE_TYPE=file` or `rtsp`
- `VIDEO_FILE_PATH=...` (absolute path recommended for Windows)
- `RTSP_URL=...` when RTSP mode
- `ZONES_PATH=./config/zones.sample.json`
- `MULTI_CAMERA_CONFIG_PATH=./config/cameras.sample.json`
- `CROSS_CAMERA_REID_ENABLED=false` (default; keep disabled in production unless a real ReID model is integrated/validated)
- `WEBHOOK_URL=` (optional)
- tuning knobs:
  - `FRAME_FPS`
  - `CONF_THRES`
  - `RISK_THRESHOLD`
  - `N_FRAMES_HAND_IN_SHELF`
  - `CONCEAL_WINDOW_SEC`
  - `EVENT_COOLDOWN_SECONDS`
  - `MODE=general_shopfloor|self_checkout`

## Run (API + UI)
Terminal 1:
```powershell
cd edgeguard
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

Terminal 2:
```powershell
cd edgeguard
python -m streamlit run apps/ui/app.py --server.address 127.0.0.1 --server.port 8501
```

Open:
- API docs: `http://127.0.0.1:8000/docs`
- UI: `http://127.0.0.1:8501`

## Run Video Debug Script
```powershell
cd edgeguard
python scripts/run_video.py --video data/videos/sample.mp4 --zones config/zones.sample.json
```

Outputs:
- annotated video in `data/debug/annotated_output.mp4`
- event json in `data/debug/annotated_output.events.json`

## Retail Theft MVP (Prerecorded Video)
```powershell
cd edgeguard
python scripts/run_video.py --video data/videos/sample.mp4 --zones config/zones.sample.json
```
Expected MVP outputs:
- temporal risk events (pick, conceal proxy, put-back, risky exit)
- per-track risk timeline in API/UI
- evidence artifacts under `data/debug/` and `data/evidence/`

## API Endpoints
- `GET /health`
- `POST /webrtc/offer` (WebRTC signaling for live video)
- `GET /events?camera_id=&event_type=&since=&until=&limit=`
- `GET /events/{event_id}`
- `GET /tracks/{track_id}/timeline`
- `GET /latest_frame?camera_id=cam01`

## Multi-Camera
Edit `config/cameras.sample.json` and add camera entries. On API startup, one worker is started per camera.
Cross-camera identity stitching is currently experimental and disabled by default.

## Webhook Alerts
Set `.env`:
```env
WEBHOOK_URL=http://your-alert-endpoint
WEBHOOK_TIMEOUT_SEC=3
```
Payload contains `event_id`, `camera_id`, `track_id`, `event_type`, `risk_score_at_trigger`, `snapshot_path`, `clip_path`, and details.

## Docker Compose
```powershell
cd edgeguard
docker compose up --build
```
Services:
- API: `http://127.0.0.1:8000`
- UI: `http://127.0.0.1:8501`

## Signals Implemented
- `NEAR_SHELF`
- `SHELF_INTERACTION`
- `HAND_TO_POCKET`
- `HAND_TO_BAG`
- `EXIT_AFTER_CONCEALMENT`
- `NONSCAN_BAGGING` (self-checkout mode)
- `SPEED_SPIKE`

## Tests
```powershell
cd edgeguard
pytest -q
```

## Known Limitations
- Occlusion-heavy scenes can break pose/keypoint quality.
- Camera angle strongly affects hand-to-pocket heuristics.
- Item-level understanding is approximated with gesture proxies.
- Single-camera design; no distributed scheduling yet.
- Cross-camera ReID is experimental (HSV histogram descriptor), not production-grade metric learning.

## Next for V1
- Multi-camera workers and per-camera queueing
- Alert connectors (webhook/Slack/SMS)
- Docker/compose deployment
- Better self-checkout verification (item flow + scan matching)
- Re-identification and cross-camera identity stitching
