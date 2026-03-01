import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException

from src.core.store_config import StoreConfig, load_store_config, validate_store_config
from src.core.config import get_settings
from src.pipeline.manager import get_pipeline_manager

router = APIRouter(prefix="/config", tags=["config"])

_store_config: StoreConfig | None = None


def _mask_rtsp_url(url: str | None) -> str:
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{host}{port}" if host else parsed.netloc
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return ""


def _resolve_safe_config_path(raw_path: str | None) -> Path:
    settings = get_settings()
    base_dir = (Path.cwd() / "config").resolve()
    configured_path = settings.multi_camera_path
    if configured_path is not None:
        base_dir = configured_path.parent.resolve()

    if raw_path is None:
        if configured_path is None:
            raise HTTPException(status_code=400, detail="No default multi-camera config path configured")
        candidate = configured_path
    else:
        candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (Path.cwd() / candidate).resolve()
    if not resolved.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Invalid config path")
    if resolved.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Config file must be a .json file")
    return resolved


@router.post("/cameras")
def set_store_config(path: str | None = None) -> dict:
    global _store_config
    try:
        safe_path = _resolve_safe_config_path(path)
        cfg = load_store_config(str(safe_path))
        validate_store_config(cfg)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Config file not found: {exc}") from exc
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid store config: {exc}") from exc
    _store_config = cfg
    return {"status": "ok", "camera_count": len(cfg.cameras)}


@router.get("/cameras")
def get_store_config() -> dict:
    settings = get_settings()
    manager = get_pipeline_manager(settings)
    live_camera_ids = manager.list_cameras()

    if _store_config is None:
        return {
            "configured": False,
            "cameras": [
                {
                    "camera_id": camera_id,
                    "role": manager.camera_settings[camera_id].camera_role,
                    "rtsp_url": _mask_rtsp_url(manager.camera_settings[camera_id].rtsp_url),
                    "zones": manager.get_camera_zones(camera_id),
                }
                for camera_id in live_camera_ids
            ],
        }

    cameras = []
    for c in _store_config.cameras:
        live_zones = manager.get_camera_zones(c.camera_id)
        merged_zones = live_zones if live_zones else c.zones
        cameras.append(
            {
                "camera_id": c.camera_id,
                "role": c.role,
                "rtsp_url": _mask_rtsp_url(c.rtsp_url),
                "zones": merged_zones,
            }
        )

    return {
        "configured": True,
        "store_id": _store_config.store_id,
        "adjacency": _store_config.adjacency,
        "cameras": cameras,
    }


@router.post("/cameras/{camera_id}/zones")
def update_camera_zones(camera_id: str, zones: dict[str, list[list[int]]]) -> dict:
    settings = get_settings()
    manager = get_pipeline_manager(settings)
    if camera_id not in manager.list_cameras():
        raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")

    try:
        normalized = {
            str(k): [(int(p[0]), int(p[1])) for p in pts]
            for k, pts in zones.items()
        }
    except (TypeError, ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid zones payload: {exc}") from exc

    manager.apply_camera_zones(camera_id, normalized)

    runner = manager.get_runner(camera_id)
    zone_path = runner.settings.zones_config_path
    payload = {
        "camera_id": camera_id,
        "zones": {name: [[x, y] for x, y in points] for name, points in normalized.items()},
    }
    zone_path.parent.mkdir(parents=True, exist_ok=True)
    zone_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if _store_config is not None:
        for c in _store_config.cameras:
            if c.camera_id == camera_id:
                c.zones = normalized
                break

    return {"status": "ok", "camera_id": camera_id}
