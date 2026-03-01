import os
import time
from collections.abc import Generator
from datetime import datetime, timedelta
from threading import Event

import cv2
import numpy as np

from src.core.logger import logger


class VideoFileSource:
    def __init__(self, path: str, loop: bool = True) -> None:
        self.path = path
        self.loop = loop

    def frames(self) -> Generator[tuple[np.ndarray, datetime], None, None]:
        while True:
            cap = cv2.VideoCapture(self.path)
            if not cap.isOpened():
                logger.error(f"Cannot open video file source: {self.path}")
                return

            logger.info(f"Reading video file source: {self.path}")
            video_start_time = datetime.utcnow()
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                # Use actual video PTS so timestamp drift doesn't accumulate (Fix 12)
                pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                ts = video_start_time + timedelta(milliseconds=pos_ms)
                yield frame, ts

            cap.release()
            if not self.loop:
                return
            logger.info("Video file reached end, restarting from beginning")


class RTSPSource:
    """RTSP source with exponential backoff on reconnect (Fix 12)."""

    def __init__(
        self,
        url: str,
        reconnect_delay_seconds: float = 1.0,
        max_delay: float = 30.0,
        stop_event: Event | None = None,
        transport: str = "tcp",
        open_timeout_ms: int = 8000,
        read_timeout_ms: int = 8000,
        buffer_size: int = 1,
        ffmpeg_options: str | None = None,
    ) -> None:
        self.url = url
        self.base_delay = reconnect_delay_seconds
        self.max_delay = max_delay
        self._stop_event = stop_event
        self.transport = transport
        self.open_timeout_ms = open_timeout_ms
        self.read_timeout_ms = read_timeout_ms
        self.buffer_size = buffer_size
        self.ffmpeg_options = ffmpeg_options

    def _open_capture(self):
        if self.ffmpeg_options:
            opts = self.ffmpeg_options
        else:
            timeout_us = int(max(self.read_timeout_ms, self.open_timeout_ms) * 1000)
            opts = (
                f"rtsp_transport;{self.transport}"
                f"|stimeout;{timeout_us}"
                f"|rw_timeout;{timeout_us}"
                "|fflags;nobuffer"
                "|flags;low_delay"
            )
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opts

        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

        if cap is not None and cap.isOpened():
            # Best-effort knobs; some builds ignore these properties.
            if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.open_timeout_ms)
            if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.read_timeout_ms)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self.buffer_size)
            return cap

        # Fallback open path if CAP_FFMPEG open fails on this build.
        if cap is not None:
            cap.release()
        cap = cv2.VideoCapture(self.url)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self.buffer_size)
        return cap

    def frames(self, stop_event: Event | None = None) -> Generator[tuple[np.ndarray, datetime], None, None]:
        _stop = stop_event or self._stop_event
        delay = self.base_delay

        while True:
            if _stop and _stop.is_set():
                return

            cap = self._open_capture()
            if not cap.isOpened():
                logger.warning(f"RTSP open failed, retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 2, self.max_delay)
                continue

            delay = self.base_delay  # reset on successful connect
            logger.info("RTSP stream connected")
            read_failures = 0

            while True:
                if _stop and _stop.is_set():
                    cap.release()
                    return
                ok, frame = cap.read()
                if not ok:
                    read_failures += 1
                    if read_failures < 3:
                        time.sleep(0.05)
                        continue
                    logger.warning("RTSP read failed repeatedly, reconnecting...")
                    break
                read_failures = 0
                yield frame, datetime.utcnow()

            cap.release()
            time.sleep(1.0)  # brief pause before reconnect attempt
