from pathlib import Path

from src.rules.zones import is_point_in_zone, load_zone_config, zone_polygons_for_frame


def test_point_in_polygon() -> None:
    polygon = [(0, 0), (100, 0), (100, 100), (0, 100)]
    assert is_point_in_zone((50, 50), polygon) is True
    assert is_point_in_zone((150, 50), polygon) is False


def test_zone_loader_fallback() -> None:
    path = Path("edgeguard/tests/_tmp_zones.json")
    if path.exists():
        path.unlink()
    cfg = load_zone_config(path=path, camera_id="cam01", frame_width=640, frame_height=360)
    assert cfg.camera_id == "cam01"
    assert "shelf_zone" in cfg.zones
    assert path.exists()
    path.unlink(missing_ok=True)


def test_zone_defaults_shape() -> None:
    zones = zone_polygons_for_frame(width=640, height=360)
    assert {"shelf_zone", "exit_zone", "checkout_zone"} <= set(zones.keys())
