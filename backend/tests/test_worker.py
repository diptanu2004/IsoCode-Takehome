"""The worker's housekeeping: files abandoned at the mapping step are deleted after a day."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db import get_engine
from app.enums import Role, UploadStatus
from app.ingest.pipeline import register_upload, upload_path
from app.ingest.worker import purge_abandoned
from app.schema import sources, uploads
from scripts.seed import DemoUser, seed
from tests.builders import AS_OF

PEOPLE = [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)]


def test_files_left_at_the_mapping_step_are_deleted_after_a_day():
    with get_engine().begin() as conn:
        seed(conn, AS_OF, PEOPLE)
        source = conn.execute(select(sources.c.id).limit(1)).scalar_one()
        old = register_upload(
            conn, source, "old.csv", b"Name,Phone\nA,5\n", None, UploadStatus.NEEDS_MAPPING
        )
        new = register_upload(
            conn, source, "new.csv", b"Name,Phone\nB,6\n", None, UploadStatus.NEEDS_MAPPING
        )
        conn.execute(
            update(uploads)
            .where(uploads.c.id == old)
            .values(created_at=datetime.now(UTC) - timedelta(hours=25))
        )
    assert upload_path(old, "old.csv").exists() and upload_path(new, "new.csv").exists()

    assert purge_abandoned() == 1
    assert not upload_path(old, "old.csv").exists()
    assert upload_path(new, "new.csv").exists()  # still inside its 24 hours
    with get_engine().begin() as conn:
        status = dict(conn.execute(select(uploads.c.id, uploads.c.status)).all())
        message = conn.execute(select(uploads.c.error_message).where(uploads.c.id == old)).scalar()
    assert status == {old: "failed", new: "needs_mapping"}
    assert "deleted" in message

    # The same file can be uploaded again, since the first attempt failed.
    with get_engine().begin() as conn:
        assert register_upload(conn, source, "old.csv", b"Name,Phone\nA,5\n", None)
    assert purge_abandoned() == 0
