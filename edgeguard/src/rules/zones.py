from dataclasses import dataclass
import json
from pathlib import Path

import cv2
import numpy as np

Point = tuple[int, int]


@dataclass
class ZoneConfig:
    camera_id: str
    zones: dict[str, list[Point]]


def centroid_from_bbox(box: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def is_point_in_zone(point: tuple[float, float], polygon: list[Point]) -> bool:
    if not polygon:
        return False
    contour = np.array(polygon, dtype=np.int32)
    return cv2.pointPolygonTest(contour, point, False) >= 0


def zone_polygons_for_frame(width: int, height: int) -> dict[str, list[Point]]:
    return {
        "shelf_zone": [
            (int(width * 0.62), int(height * 0.10)),
            (int(width * 0.95), int(height * 0.10)),
            (int(width * 0.95), int(height * 0.65)),
            (int(width * 0.62), int(height * 0.65)),
        ],
        "exit_zone": [
            (int(width * 0.00), int(height * 0.00)),
            (int(width * 0.24), int(height * 0.00)),
            (int(width * 0.24), int(height * 0.28)),
            (int(width * 0.00), int(height * 0.28)),
        ],
        "checkout_zone": [
            (int(width * 0.08), int(height * 0.55)),
            (int(width * 0.46), int(height * 0.55)),
            (int(width * 0.46), int(height * 0.95)),
            (int(width * 0.08), int(height * 0.95)),
        ],
        "bagging_zone": [
            (int(width * 0.48), int(height * 0.55)),
            (int(width * 0.68), int(height * 0.55)),
            (int(width * 0.68), int(height * 0.92)),
            (int(width * 0.48), int(height * 0.92)),
        ],
        "scanner_zone": [
            (int(width * 0.70), int(height * 0.60)),
            (int(width * 0.82), int(height * 0.60)),
            (int(width * 0.82), int(height * 0.80)),
            (int(width * 0.70), int(height * 0.80)),
        ],
    }


def load_zone_config(path: Path, camera_id: str, frame_width: int, frame_height: int) -> ZoneConfig:
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        zones_raw = payload.get("zones", {})
        zones: dict[str, list[Point]] = {}
        for name, pts in zones_raw.items():
            zones[name] = [(int(p[0]), int(p[1])) for p in pts]
        return ZoneConfig(camera_id=payload.get("camera_id", camera_id), zones=zones)

    path.parent.mkdir(parents=True, exist_ok=True)
    zones = zone_polygons_for_frame(width=frame_width, height=frame_height)
    sample = {
        "camera_id": camera_id,
        "zones": {name: [[x, y] for x, y in polygon] for name, polygon in zones.items()},
    }
    path.write_text(json.dumps(sample, indent=2), encoding="utf-8")
    return ZoneConfig(camera_id=camera_id, zones=zones)
