from dataclasses import dataclass
from datetime import datetime
from queue import Queue
from threading import Event, Thread

import numpy as np

from src.video.sources import RTSPSource


@dataclass
class FrameEvent:
    camera_id: str
    role: str
    ts: datetime
    frame: np.ndarray


class CameraWorker:
    def __init__(self, camera_cfg, out_queue: Queue) -> None:
        self.camera_cfg = camera_cfg
        self.out_queue = out_queue
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, daemon=True, name=f"camera-worker-{self.camera_cfg.camera_id}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run(self) -> None:
        source = RTSPSource(self.camera_cfg.rtsp_url)
        for frame, ts in source.frames():
            if self._stop.is_set():
                break
            self.out_queue.put(
                FrameEvent(
                    camera_id=self.camera_cfg.camera_id,
                    role=self.camera_cfg.role,
                    ts=ts,
                    frame=frame,
                )
            )
