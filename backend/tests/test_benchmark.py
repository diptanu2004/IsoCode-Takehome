"""A small benchmark in the test suite, to catch a bad query plan early.

One statement once went from 21 s to 20 minutes because of a planner misestimate. A plan that bad
shows up even at this size. The real 1 lakh-row figure comes from scripts/benchmark.py.
"""

from datetime import date

from sqlalchemy import select

from app.db import get_engine
from app.enums import Role
from app.ingest import worker
from app.schema import app_meta
from scripts.benchmark import reset_data, run
from scripts.seed import DemoUser, seed

AS_OF = date(2026, 11, 3)


def test_pipeline_stays_fast(tmp_path):
    results = run(5000, ["csv"], AS_OF, tmp_path)
    orders, settlement = results
    assert settlement.rows == 5000 and settlement.lines > 20000
    assert orders.seconds < 30
    assert settlement.seconds < 30
    assert settlement.stages["match"] < 15


def test_daily_sweep_runs_once_a_day():
    reset_data(AS_OF)
    assert worker.sweep_if_new_day() is True
    assert worker.sweep_if_new_day() is False
    with get_engine().begin() as conn:
        stored = conn.execute(select(app_meta.c.value).where(app_meta.c.key == "swept_on")).scalar()
    assert stored == worker.today().isoformat()


def test_a_file_over_the_row_limit_is_refused(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.ingest.pipeline import process_upload, register_upload
    from app.schema import sources, uploads

    monkeypatch.setattr(get_settings(), "max_upload_rows", 2)
    with get_engine().begin() as conn:
        seed(conn, AS_OF, [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)])
        source_id = conn.execute(select(sources.c.id).limit(1)).scalar()
        upload_id = register_upload(conn, source_id, "big.csv", b"a,b\n1,2\n3,4\n5,6\n", None)
    assert process_upload(upload_id, AS_OF) == "failed"
    with get_engine().begin() as conn:
        message = conn.execute(
            select(uploads.c.error_message).where(uploads.c.id == upload_id)
        ).scalar()
    assert "the limit is 2" in message
