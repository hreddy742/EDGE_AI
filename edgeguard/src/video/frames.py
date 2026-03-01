from datetime import datetime


class FrameSampler:
    def __init__(self, target_fps: int) -> None:
        self.target_fps = max(1, target_fps)
        self.interval = 1.0 / float(self.target_fps)
        self._last_ts: float | None = None

    def should_process(self, ts: datetime) -> bool:
        now = ts.timestamp()
        if self._last_ts is None:
            self._last_ts = now
            return True

        if (now - self._last_ts) >= self.interval:
            self._last_ts = now
            return True
        return False

    def force_skip(self) -> None:
        """Advance the internal clock by one interval so the next frame is skipped.

        Call this when the pipeline fell behind budget so stale frames are
        discarded instead of queued up.
        """
        if self._last_ts is not None:
            self._last_ts += self.interval
