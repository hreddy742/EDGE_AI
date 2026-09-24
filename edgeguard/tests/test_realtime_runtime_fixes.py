from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.api import routes_config, routes_reconcile
from src.core.config import Settings
from src.pipeline.runner import PipelineRunner


def test_track_id_parser_handles_global_customer_ids() -> None:
    assert PipelineRunner._track_id_from_customer_id("cam01:17") == 17
    assert PipelineRunner._track_id_from_customer_id("CUST-2ea6f99d-2b9e-4af1-b893-6d0454b4f5e6") == 0
    assert PipelineRunner._track_id_from_customer_id("cam01:not-int") == 0


def test_update_camera_zones_works_without_store_config(monkeypatch: pytest.MonkeyPatch) -> None:
    zones_file = Path("tests/_tmp_runtime_zones_cam01.json")
    zones_file.unlink(missing_ok=True)

    class DummyRunner:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(zones_config_path=zones_file)

    class DummyManager:
        def __init__(self) -> None:
            self.runner = DummyRunner()
            self.camera_settings = {"cam01": SimpleNamespace(camera_role="AISLE", rtsp_url="")}
            self.applied: dict[str, dict] = {}

        def list_cameras(self) -> list[str]:
            return ["cam01"]

        def apply_camera_zones(self, camera_id: str, zones: dict) -> None:
            self.applied[camera_id] = zones

        def get_runner(self, camera_id: str | None = None) -> DummyRunner:
            return self.runner

        def get_camera_zones(self, camera_id: str) -> dict:
            return self.applied.get(camera_id, {})

    manager = DummyManager()
    monkeypatch.setattr(routes_config, "get_settings", lambda: Settings(run_pipeline_on_startup=False))
    monkeypatch.setattr(routes_config, "get_pipeline_manager", lambda _settings: manager)
    monkeypatch.setattr(routes_config, "_store_config", None)

    payload = {"shelf_zone": [[10, 10], [20, 10], [20, 20], [10, 20]]}
    response = routes_config.update_camera_zones("cam01", payload)

    assert response["status"] == "ok"
    assert "cam01" in manager.applied
    assert manager.runner.settings.zones_config_path.exists()
    manager.runner.settings.zones_config_path.unlink(missing_ok=True)


def test_reconcile_counter_uses_presented_unknown_count(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def reconcile_counter(self, customer_id: str, presented_item_ids: list[str], presented_unknown_count: int) -> dict:
            self.calls.append(
                {
                    "customer_id": customer_id,
                    "presented_item_ids": presented_item_ids,
                    "presented_unknown_count": presented_unknown_count,
                }
            )
            return {
                "customer_id": customer_id,
                "missing_count": 0,
                "missing_item_ids": [],
                "resolved": True,
            }

    class DummyManager:
        def __init__(self) -> None:
            self.runner = DummyRunner()

        def get_runner(self, camera_id: str | None = None) -> DummyRunner:
            return self.runner

    manager = DummyManager()
    monkeypatch.setattr(routes_reconcile, "get_settings", lambda: Settings(run_pipeline_on_startup=False))
    monkeypatch.setattr(routes_reconcile, "get_pipeline_manager", lambda _settings: manager)

    payload = routes_reconcile.CounterReconcileRequest(
        customer_id="CUST-abc",
        camera_id="cam01",
        presented_item_ids=["item-1"],
        presented_unknown_count=2,
    )
    response = routes_reconcile.reconcile_counter(payload)

    assert response.missing_count == 0
    assert manager.runner.calls[0]["presented_unknown_count"] == 2


def test_set_store_config_returns_404_for_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as exc:
        routes_config.set_store_config(path="missing-config-file.json")
    assert exc.value.status_code == 404
