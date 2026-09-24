from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

CameraRole = Literal["ENTRY_EXIT", "AISLE", "COUNTER"]


@dataclass
class CameraConfig:
    camera_id: str
    role: CameraRole
    rtsp_url: str
    zones: dict[str, list[tuple[int, int]]]


@dataclass
class StoreConfig:
    store_id: str
    cameras: list[CameraConfig]
    adjacency: dict[str, list[str]]


def load_store_config(path: str) -> StoreConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cameras: list[CameraConfig] = []
    for c in payload.get("cameras", []):
        zones: dict[str, list[tuple[int, int]]] = {}
        for name, pts in c.get("zones", {}).items():
            zones[name] = [(int(p[0]), int(p[1])) for p in pts]
        role = str(c.get("role") or c.get("camera_role") or "AISLE")
        rtsp_url = str(c.get("rtsp_url") or "")
        cameras.append(
            CameraConfig(
                camera_id=str(c["camera_id"]),
                role=role,
                rtsp_url=rtsp_url,
                zones=zones,
            )
        )
    return StoreConfig(
        store_id=str(payload.get("store_id", "store-001")),
        cameras=cameras,
        adjacency={str(k): [str(v) for v in vals] for k, vals in payload.get("adjacency", {}).items()},
    )


def validate_store_config(cfg: StoreConfig) -> None:
    if not cfg.cameras:
        raise ValueError("Store config must include at least one camera.")
    roles = [c.role for c in cfg.cameras]
    allowed_roles = {"ENTRY_EXIT", "AISLE", "COUNTER"}
    unknown = [r for r in roles if r not in allowed_roles]
    if unknown:
        raise ValueError(f"Invalid camera role(s): {unknown}")
