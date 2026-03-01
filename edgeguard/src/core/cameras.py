import json
from pathlib import Path

from pydantic import BaseModel

from src.core.config import Settings


class CameraSourceConfig(BaseModel):
    camera_id: str
    camera_role: str | None = None
    mode: str | None = None
    video_source_type: str = "file"
    video_file_path: str | None = None
    rtsp_url: str | None = None
    zones_path: str | None = None


def load_camera_configs(base_settings: Settings) -> list[Settings]:
    path = base_settings.multi_camera_path
    if path is None or not path.exists():
        return [base_settings]

    payload = json.loads(path.read_text(encoding="utf-8"))
    cameras = payload.get("cameras", [])
    if not cameras:
        return [base_settings]

    out: list[Settings] = []
    for item in cameras:
        cfg = CameraSourceConfig(**item)
        update = {
            "camera_id": cfg.camera_id,
            "camera_role": cfg.camera_role or base_settings.camera_role,
            "mode": cfg.mode or base_settings.mode,
            "video_source_type": cfg.video_source_type,
            "video_file_path": cfg.video_file_path or base_settings.video_file_path,
            "rtsp_url": cfg.rtsp_url or base_settings.rtsp_url,
            "zones_path": cfg.zones_path or base_settings.zones_path,
        }
        out.append(base_settings.model_copy(update=update))
    return out
