"""Background worker: takes queued uploads from Postgres and processes them one at a time.

There is no separate queue service. `uploads` is the queue, and SELECT … FOR UPDATE SKIP LOCKED
lets a second process start without ever taking the same upload. One job at a time keeps two
loads from racing on the same orders.
"""

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import get_engine
from app.enums import UploadStatus
from app.ingest.pipeline import process_upload
from app.recon.engine import match_pending, today
from app.schema import app_meta

log = logging.getLogger(__name__)

IDLE_SECONDS = 1.0
STUCK_AFTER = timedelta(minutes=15)


def claim_next() -> int | None:
    with get_engine().begin() as conn:
        return conn.execute(
            text(
                """
                UPDATE uploads SET status = 'loading', started_at = now()
                WHERE id = (
                    SELECT id FROM uploads WHERE status = 'queued'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id
                """
            )
        ).scalar()


def requeue_stuck() -> int:
    """Uploads left mid-load by a restart go back on the queue (the load was rolled back)."""
    with get_engine().begin() as conn:
        return conn.execute(
            text(
                "UPDATE uploads SET status = 'queued', started_at = NULL "
                "WHERE status IN ('loading', 'matching') AND started_at < :before"
            ),
            {"before": datetime.now(UTC) - STUCK_AFTER},
        ).rowcount


def run_one() -> int | None:
    upload_id = claim_next()
    if upload_id is None:
        return None
    try:
        process_upload(upload_id)
    except Exception:
        log.exception("Upload %s failed", upload_id)
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "UPDATE uploads SET status = :failed, finished_at = now(), "
                    "error_message = :message WHERE id = :id"
                ),
                {
                    "failed": UploadStatus.FAILED.value,
                    "id": upload_id,
                    "message": "Something went wrong while loading this file. Nothing was "
                    "saved from it. Try again, and if it fails again, let us know.",
                },
            )
    return upload_id


def sweep_if_new_day() -> bool:
    """Once a day, re-check orders waiting on a date: time alone can make a payment overdue."""
    day = today().isoformat()
    with get_engine().begin() as conn:
        last = conn.execute(select(app_meta.c.value).where(app_meta.c.key == "swept_on")).scalar()
        if last == day:
            return False
        match_pending(conn)
        conn.execute(
            pg_insert(app_meta)
            .values(key="swept_on", value=day)
            .on_conflict_do_update(index_elements=["key"], set_={"value": day})
        )
    return True


class Worker(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="upload-worker", daemon=True)
        self._stopping = threading.Event()

    def run(self) -> None:
        requeue_stuck()
        while not self._stopping.is_set():
            try:
                if run_one() is None:
                    sweep_if_new_day()
                    self._stopping.wait(IDLE_SECONDS)
            except Exception:  # never let the thread die; the database may be restarting
                log.exception("Upload worker error")
                self._stopping.wait(5)

    def stop(self) -> None:
        self._stopping.set()
