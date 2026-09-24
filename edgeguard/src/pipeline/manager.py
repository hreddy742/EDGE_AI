import json

from src.core.cameras import load_camera_configs
from src.core.config import Settings
from src.core.logger import logger
from src.fusion.global_identity import GlobalIdentityResolver
from src.pipeline.runner import PipelineRunner


class PipelineManager:
    def __init__(self, base_settings: Settings) -> None:
        self.base_settings = base_settings
        self.runners: dict[str, PipelineRunner] = {}
        self.camera_settings: dict[str, Settings] = {}
        self.identity_resolver = GlobalIdentityResolver(
            adjacency=self._load_adjacency(),
            enable_cross_camera_match=base_settings.cross_camera_reid_enabled,
        )
        if not base_settings.cross_camera_reid_enabled:
            logger.warning("Cross-camera ReID matching is disabled (experimental feature).")
        self._init_runners()

    def _load_adjacency(self) -> dict[str, list[str]]:
        path = self.base_settings.multi_camera_path
        if path is None or not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw = payload.get("adjacency", {})
            return {str(k): [str(v) for v in vals] for k, vals in raw.items()}
        except Exception:
            return {}

    def _init_runners(self) -> None:
        for settings in load_camera_configs(self.base_settings):
            self.camera_settings[settings.camera_id] = settings
            self.runners[settings.camera_id] = PipelineRunner(settings, identity_resolver=self.identity_resolver)
        logger.info(f"Initialized pipeline workers for cameras: {list(self.runners.keys())}")

    def start_all(self) -> None:
        for camera_id, runner in self.runners.items():
            logger.info(f"Starting pipeline runner: {camera_id}")
            runner.start()

    def stop_all(self) -> None:
        for camera_id, runner in self.runners.items():
            logger.info(f"Stopping pipeline runner: {camera_id}")
            runner.stop()

    def get_runner(self, camera_id: str | None = None) -> PipelineRunner:
        if camera_id and camera_id in self.runners:
            return self.runners[camera_id]
        default_id = self.base_settings.camera_id
        if default_id in self.runners:
            return self.runners[default_id]
        return next(iter(self.runners.values()))

    def list_cameras(self) -> list[str]:
        return list(self.runners.keys())

    def apply_camera_zones(self, camera_id: str, zones: dict[str, list[tuple[int, int]]]) -> None:
        runner = self.get_runner(camera_id)
        runner.update_zones(zones)

    def get_camera_zones(self, camera_id: str) -> dict[str, list[tuple[int, int]]]:
        runner = self.get_runner(camera_id)
        return runner.get_zones()


_manager_singleton: PipelineManager | None = None


def get_pipeline_manager(settings: Settings) -> PipelineManager:
    global _manager_singleton
    if _manager_singleton is None:
        _manager_singleton = PipelineManager(settings)
    return _manager_singleton
