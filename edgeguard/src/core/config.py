from functools import lru_cache
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    mode: Literal["general_shopfloor", "self_checkout"] = "general_shopfloor"
    video_source_type: Literal["file", "rtsp"] = "file"
    video_file_path: str = "./data/videos/sample.mp4"
    rtsp_url: str = "rtsp://user:pass@127.0.0.1:554/stream"
    camera_id: str = "cam01"
    camera_role: Literal["ENTRY_EXIT", "AISLE", "COUNTER"] = "AISLE"
    zones_path: str = "./config/zones.sample.json"
    multi_camera_config_path: str | None = "./config/cameras.sample.json"

    frame_fps: int = Field(default=12, ge=1, le=60)
    stream_jpeg_max_fps: int = Field(default=20, ge=1, le=60)
    drop_frames_when_lagging: bool = False
    rtsp_transport: Literal["tcp", "udp"] = "tcp"
    rtsp_open_timeout_ms: int = Field(default=8000, ge=1000, le=120000)
    rtsp_read_timeout_ms: int = Field(default=8000, ge=1000, le=120000)
    rtsp_buffer_size: int = Field(default=1, ge=1, le=16)
    rtsp_ffmpeg_options: str | None = None
    model_name: str = "yolo26n.pt"
    pose_model_name: str = "yolo26n-pose.pt"
    conf_thres: float = Field(default=0.35, ge=0.05, le=0.95)
    iou_thres: float = Field(default=0.5, ge=0.1, le=0.95)
    jpeg_quality: int = Field(default=75, ge=10, le=100)

    risk_threshold: float = Field(default=8.0, ge=1.0, le=100.0)
    n_frames_hand_in_shelf: int = Field(default=4, ge=1, le=120)
    conceal_window_sec: int = Field(default=4, ge=1, le=60)
    event_cooldown_seconds: int = Field(default=20, ge=1, le=3600)
    hand_to_hip_distance_px: float = Field(default=110.0, ge=20.0, le=500.0)
    wrist_zone_conf_thres: float = Field(default=0.20, ge=0.01, le=1.0)

    db_url: str = "sqlite:///./edgeguard.db"
    snapshot_dir: str = "./data/snapshots"
    clip_dir: str = "./data/clips"
    debug_dump_dir: str = "./data/debug"
    debug_track_id: int | None = None
    theft_clip_seconds_before: int = Field(default=2, ge=0, le=30)
    theft_clip_seconds_after: int = Field(default=3, ge=1, le=60)
    theft_alert_seconds: int = Field(default=4, ge=1, le=30)

    item_detection_enabled: bool = True   # shares the person-detection YOLO model, zero extra cost
    use_bytetrack: bool = True
    cross_camera_reid_enabled: bool = False
    run_pipeline_on_startup: bool = True
    api_base_url: str = "http://127.0.0.1:8000"
    api_key: str | None = None
    webhook_url: str | None = None
    webhook_timeout_sec: float = Field(default=3.0, ge=0.5, le=30.0)

    def model_post_init(self, __context: Any) -> None:
        # Pre-compute all path properties once at construction time so that
        # repeated accesses (dozens of times per frame) never hit the filesystem.
        object.__setattr__(self, "_snapshot_path", Path(self.snapshot_dir).resolve())
        object.__setattr__(self, "_clip_path", Path(self.clip_dir).resolve())
        object.__setattr__(self, "_debug_dump_path", Path(self.debug_dump_dir).resolve())

        raw_zones = Path(self.zones_path)
        zones_path = raw_zones if raw_zones.is_absolute() else (Path.cwd() / raw_zones).resolve()
        object.__setattr__(self, "_zones_config_path", zones_path)

        raw_video = Path(self.video_file_path)
        video_path = raw_video if raw_video.is_absolute() else (Path.cwd() / raw_video).resolve()
        object.__setattr__(self, "_video_path", video_path)

        if self.multi_camera_config_path:
            raw_mc = Path(self.multi_camera_config_path)
            mc_path = raw_mc if raw_mc.is_absolute() else (Path.cwd() / raw_mc).resolve()
        else:
            mc_path = None
        object.__setattr__(self, "_multi_camera_path", mc_path)

    @property
    def snapshot_path(self) -> Path:
        return self._snapshot_path  # type: ignore[return-value]

    @property
    def clip_path(self) -> Path:
        return self._clip_path  # type: ignore[return-value]

    @property
    def debug_dump_path(self) -> Path:
        return self._debug_dump_path  # type: ignore[return-value]

    @property
    def zones_config_path(self) -> Path:
        return self._zones_config_path  # type: ignore[return-value]

    @property
    def video_path(self) -> Path:
        return self._video_path  # type: ignore[return-value]

    @property
    def multi_camera_path(self) -> Path | None:
        return self._multi_camera_path  # type: ignore[return-value]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    os.environ.setdefault("YOLO_VERBOSE", "False")
    return Settings()
