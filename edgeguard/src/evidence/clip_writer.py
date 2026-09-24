from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np


@dataclass
class BufferedFrame:
    ts: datetime
    frame: np.ndarray


class RollingFrameBuffer:
    def __init__(self, seconds: int = 60, fps: int = 15) -> None:
        self.maxlen = max(1, seconds * fps)
        self.frames: deque[BufferedFrame] = deque(maxlen=self.maxlen)

    def append(self, ts: datetime, frame: np.ndarray) -> None:
        self.frames.append(BufferedFrame(ts=ts, frame=frame.copy()))

    def slice(self, start_ts: datetime, end_ts: datetime) -> list[np.ndarray]:
        return [f.frame for f in self.frames if start_ts <= f.ts <= end_ts]


class ClipWriter:
    def __init__(self, output_dir: str = "data/evidence") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_event_clip(
        self,
        camera_id: str,
        event_id: str,
        ts_start: datetime,
        ts_end: datetime,
        buffer: RollingFrameBuffer,
        prebuffer_sec: int = 5,
        postbuffer_sec: int = 5,
        fps: int = 15,
    ) -> str | None:
        start = ts_start - timedelta(seconds=prebuffer_sec)
        end = ts_end + timedelta(seconds=postbuffer_sec)
        frames = buffer.slice(start, end)
        if not frames:
            return None

        h, w = frames[0].shape[:2]
        clip_path = self.output_dir / f"{camera_id}_{event_id}.mp4"
        writer = cv2.VideoWriter(str(clip_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for frame in frames:
            writer.write(frame)
        writer.release()
        return str(clip_path)

    def write_frames(self, clip_name: str, frames: list[np.ndarray], fps: int = 15) -> str | None:
        if not frames:
            return None
        h, w = frames[0].shape[:2]
        clip_path = self.output_dir / f"{clip_name}.mp4"
        writer = cv2.VideoWriter(str(clip_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()
        return str(clip_path)
