"""Background DB write worker.

Drains a bounded queue in a daemon thread, batching high-frequency
writes (signals, track_points) and committing lower-frequency writes
(customer/item upserts, events, clips) individually.
"""
from __future__ import annotations

import json
import queue
import time
import uuid
from threading import Event, Thread
from typing import Any

from src.core.logger import logger
from src.store import crud
from src.store.models import SignalRecord, TrackTimelineRecord

_BATCH_FLUSH_INTERVAL = 0.5   # seconds between forced batch commits
_BATCH_SIZE = 50              # max items before immediate flush


class DBWriteWorker:
    def __init__(self, session_factory: Any) -> None:
        self._queue: queue.Queue[dict | None] = queue.Queue(maxsize=2000)
        self._stop = Event()
        self._session_factory = session_factory
        self._thread = Thread(target=self._run, name="db-writer", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def put(self, task_type: str, payload: dict) -> None:
        """Queue a DB write task with backpressure instead of dropping."""
        task = {"type": task_type, "payload": payload}
        warned = False
        while not self._stop.is_set():
            try:
                self._queue.put(task, timeout=0.25)
                return
            except queue.Full:
                if not warned:
                    logger.warning("DB write queue saturated; applying backpressure for %s", task_type)
                    warned = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _flush_batch(
        self,
        db: Any,
        batch_signals: list[dict],
        batch_points: list[dict],
    ) -> None:
        if batch_signals:
            try:
                records = [
                    SignalRecord(
                        signal_id=str(uuid.uuid4()),
                        event_id=p.get("event_id"),
                        camera_id=p["camera_id"],
                        track_id=p["track_id"],
                        signal_type=p["signal_type"],
                        ts=p["ts"],
                        value=p.get("value", 0.0),
                        details=json.dumps(p.get("details", {})),
                    )
                    for item in batch_signals
                    for p in [item["payload"]]
                ]
                db.bulk_save_objects(records)
                db.commit()
                batch_signals.clear()
            except Exception as exc:
                logger.warning("DB writer signal batch flush failed: %s", exc)
                try:
                    db.rollback()
                except Exception:
                    pass

        if batch_points:
            try:
                records = [
                    TrackTimelineRecord(
                        camera_id=p["camera_id"],
                        track_id=p["track_id"],
                        ts=p["ts"],
                        risk_score=p["risk_score"],
                        state=p["state"],
                        centroid_x=p.get("centroid_x", 0.0),
                        centroid_y=p.get("centroid_y", 0.0),
                        velocity=p.get("velocity", 0.0),
                        details=json.dumps(p.get("details", {})),
                    )
                    for item in batch_points
                    for p in [item["payload"]]
                ]
                db.bulk_save_objects(records)
                db.commit()
                batch_points.clear()
            except Exception as exc:
                logger.warning("DB writer track_point batch flush failed: %s", exc)
                try:
                    db.rollback()
                except Exception:
                    pass

    def _handle_task(
        self,
        db: Any,
        task: dict,
        batch_signals: list[dict],
        batch_points: list[dict],
    ) -> None:
        task_type = task["type"]
        payload = task["payload"]

        if task_type == "signal":
            batch_signals.append(task)
            if len(batch_signals) >= _BATCH_SIZE:
                self._flush_batch(db, batch_signals, [])
            return

        if task_type == "track_point":
            batch_points.append(task)
            if len(batch_points) >= _BATCH_SIZE:
                self._flush_batch(db, [], batch_points)
            return

        # For all other task types flush pending batches first so ordering is sane.
        if batch_signals or batch_points:
            self._flush_batch(db, batch_signals, batch_points)

        try:
            if task_type == "customer_upsert":
                crud.upsert_customer(db=db, **payload)
            elif task_type == "item_upsert":
                crud.upsert_item(db=db, **payload)
            elif task_type == "event":
                crud.create_event(db=db, **payload)
            elif task_type == "clip_create":
                crud.create_clip(db=db, **payload)
            elif task_type == "clip_update":
                crud.update_clip_processing(db=db, **payload)
            else:
                logger.warning("DB writer unknown task type: %s", task_type)
        except Exception as exc:
            logger.warning("DB writer %s failed: %s", task_type, exc)
            try:
                db.rollback()
            except Exception:
                pass

    def _run(self) -> None:
        db = self._session_factory()
        batch_signals: list[dict] = []
        batch_points: list[dict] = []
        last_flush = time.monotonic()

        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    task = self._queue.get(timeout=0.1)
                    self._handle_task(db, task, batch_signals, batch_points)
                except queue.Empty:
                    pass

                now = time.monotonic()
                if now - last_flush >= _BATCH_FLUSH_INTERVAL:
                    self._flush_batch(db, batch_signals, batch_points)
                    last_flush = now

            # Final drain flush
            self._flush_batch(db, batch_signals, batch_points)
        except Exception as exc:
            logger.exception("DB write worker crashed: %s", exc)
        finally:
            db.close()
