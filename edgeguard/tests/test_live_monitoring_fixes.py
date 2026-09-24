import queue
import threading
import time

from src.core.config import Settings
from src.store.db_writer import DBWriteWorker


def test_settings_expose_realtime_tuning_flags() -> None:
    settings = Settings(run_pipeline_on_startup=False)
    assert settings.stream_jpeg_max_fps >= 1
    assert settings.drop_frames_when_lagging is False


def test_db_writer_put_applies_backpressure_instead_of_drop() -> None:
    worker = DBWriteWorker(session_factory=lambda: None)
    worker._queue = queue.Queue(maxsize=1)  # shrink queue to force backpressure quickly
    worker._queue.put({"type": "preloaded", "payload": {}})

    def release_slot() -> None:
        time.sleep(0.05)
        worker._queue.get_nowait()

    t = threading.Thread(target=release_slot, daemon=True)
    t.start()

    start = time.monotonic()
    worker.put("signal", {"camera_id": "cam01"})
    elapsed = time.monotonic() - start

    assert elapsed >= 0.04
    queued = worker._queue.get_nowait()
    assert queued["type"] == "signal"
