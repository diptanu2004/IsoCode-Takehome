"""Time the whole pipeline on large files, in a database of its own.

    uv run python -m scripts.benchmark               # a 1 lakh-row settlement, as CSV and as Excel
    uv run python -m scripts.benchmark --rows 20000 --formats csv

Times run from the file being picked up to matching and claims being finished. The target from the
brief is under 60 s for 1 lakh rows. Uses the database named recon_bench on the same server as
DATABASE_URL, and var/bench_uploads, so it never touches the app's own data.
"""

import argparse
import dataclasses
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from app.config import REPO_ROOT, Settings, get_settings
from app.db import get_engine
from app.enums import Role, UploadStatus
from app.ingest.pipeline import process_upload, register_upload
from app.schema import metadata, sources, uploads
from scripts.catalog import M1, ORDERS_FORMAT, SEED_FORMATS, FileFormat
from scripts.generate_data import _order_row, generate, write_file
from scripts.seed import DemoUser, seed

BACKEND_DIR = Path(__file__).resolve().parents[1]
BENCH_DB = "recon_bench"
SETTLED_SHARE = 0.75  # about this share of orders has a settlement line by the as-of date
TARGET_SECONDS = 60
PEOPLE = [DemoUser("bench@demo.test", "Bench", "bench-password", Role.COFOUNDER)]
M1_SETTLEMENT = next(f for f in SEED_FORMATS if f.partner == M1)


@dataclass
class Result:
    name: str
    rows: int
    lines: int
    seconds: float
    stages: dict


def use_bench_database() -> None:
    """Point the app at the benchmark database, creating and migrating it if needed."""
    url = make_url(Settings().sqlalchemy_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        found = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": BENCH_DB}
        ).scalar()
        if not found:
            conn.execute(text(f'CREATE DATABASE "{BENCH_DB}"'))
    admin.dispose()

    os.environ["DATABASE_URL"] = url.set(database=BENCH_DB).render_as_string(hide_password=False)
    os.environ["UPLOAD_DIR"] = str(REPO_ROOT / "var" / "bench_uploads")
    get_settings.cache_clear()
    get_engine.cache_clear()
    with get_engine().begin() as conn:
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.attributes["connection"] = conn
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")


def reset_data(as_of: date) -> None:
    names = ", ".join(t.name for t in metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        seed(conn, as_of, PEOPLE)


def timed_upload(fmt: FileFormat, path: Path, as_of: date) -> Result:
    with get_engine().begin() as conn:
        source_id = conn.execute(
            select(sources.c.id).where(sources.c.name == fmt.source_name)
        ).scalar_one()
        upload_id = register_upload(
            conn, source_id, path.name, path.read_bytes(), None, status=UploadStatus.LOADING
        )
    started = time.perf_counter()
    status = process_upload(upload_id, as_of)
    seconds = time.perf_counter() - started
    with get_engine().begin() as conn:
        row = conn.execute(
            select(uploads.c.row_count, uploads.c.stats, uploads.c.stage_timings).where(
                uploads.c.id == upload_id
            )
        ).one()
    if status != "done":
        raise RuntimeError(f"{path.name} ended as {status}")
    return Result(path.name, row.row_count, row.stats.get("lines", 0), seconds, row.stage_timings)


def run(rows: int, formats: list[str], as_of: date, workdir: Path) -> list[Result]:
    """Load an orders export, then a settlement file of exactly `rows` rows against it."""
    data = generate(as_of, int(rows / SETTLED_SHARE) + 1, days=60, seed=3, channels={M1: 1})
    settlement = sorted(data.rows[M1], key=lambda r: r["txn_date"])
    if len(settlement) < rows:
        raise RuntimeError(f"Only {len(settlement)} settlement rows were generated.")
    settlement = settlement[:rows]

    orders_file = workdir / ORDERS_FORMAT.filename
    order_rows = [_order_row(o, as_of) for o in data.orders]
    write_file(ORDERS_FORMAT, order_rows, orders_file, as_of)

    results = []
    for kind in formats:
        fmt = (
            dataclasses.replace(M1_SETTLEMENT, excel=True, filename="settlement_bench.xlsx")
            if kind == "xlsx"
            else dataclasses.replace(M1_SETTLEMENT, filename="settlement_bench.csv")
        )
        path = workdir / fmt.filename
        write_file(fmt, settlement, path, as_of)
        reset_data(as_of)
        results.append(timed_upload(ORDERS_FORMAT, orders_file, as_of))
        results.append(timed_upload(fmt, path, as_of))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--formats", nargs="+", default=["csv", "xlsx"], choices=["csv", "xlsx"])
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    use_bench_database()
    get_settings().upload_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        results = run(args.rows, args.formats, args.as_of, Path(tmp))

    stages = ("read", "map", "store_rows", "load", "link", "match")
    print(f"\n{'file':34}{'rows':>9}{'lines':>9}" + "".join(f"{s:>11}" for s in stages) + "  total")
    for r in results:
        cells = "".join(f"{r.stages.get(s, 0):>10.1f}s" for s in stages)
        verdict = "" if r.seconds < TARGET_SECONDS else "  OVER TARGET"
        print(f"{r.name:34}{r.rows:>9,}{r.lines:>9,}{cells}  {r.seconds:.1f}s{verdict}")


if __name__ == "__main__":
    main()
