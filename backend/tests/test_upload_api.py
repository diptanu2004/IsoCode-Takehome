import io
import zipfile
from datetime import date

import pytest
from sqlalchemy import select

from app.db import get_engine
from app.enums import Role
from app.exports import safe_cell
from app.ingest import worker
from app.schema import sources
from scripts.seed import DemoUser, seed

HEADER = (
    "Order ID,Settlement Date,Sale Amount,Commission,Closing Fee,Shipping Fee,"
    "Reverse Shipping Fee,TCS,TDS,Other Adjustments,Net Payable,UTR\n"
)
GOOD_ROW = "ORD-M-80001,2026-10-01,1199.00,180,30,90,0,5.71,1.20,0,892.09,U1\n"


@pytest.fixture
def exec_client(signed_in):
    with get_engine().begin() as conn:
        seed(
            conn, date(2026, 11, 3), [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)]
        )
    return signed_in(Role.EXEC)


def _source_id(name="Marketplace 1 · Settlement") -> int:
    with get_engine().begin() as conn:
        return conn.execute(select(sources.c.id).where(sources.c.name == name)).scalar_one()


def _post(client, content: bytes, filename="m1.csv", source_id=None):
    return client.post(
        "/api/uploads",
        data={"source_id": str(source_id or _source_id())},
        files={"file": (filename, content, "application/octet-stream")},
    )


def test_upload_is_queued_then_processed_by_the_worker(exec_client):
    response = _post(exec_client, (HEADER + GOOD_ROW).encode())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued" and body["source"] == "Marketplace 1 · Settlement"

    assert worker.run_one() == body["id"]
    done = exec_client.get(f"/api/uploads/{body['id']}").json()
    assert done["status"] == "done"
    assert done["stats"]["lines_created"] == 6
    assert done["uploaded_by"] == "Test exec"
    assert worker.run_one() is None  # queue is empty


def test_same_file_twice_is_a_conflict(exec_client):
    content = (HEADER + GOOD_ROW).encode()
    assert _post(exec_client, content).status_code == 201
    response = _post(exec_client, content, "renamed.csv")
    assert response.status_code == 409
    assert "already uploaded" in response.json()["detail"]


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("report.pdf", b"%PDF-1.7", "Upload a .csv or .xlsx file."),
        ("old.xls", b"\xd0\xcf\x11\xe0", "Old .xls files aren't supported"),
        ("fake.xlsx", b"just text", "This isn't a valid .xlsx file"),
        ("binary.csv", b"a,b\n\x00\x01\x02", "This doesn't look like a CSV file"),
        ("empty.csv", b"", "The file is empty."),
    ],
)
def test_files_are_checked_by_content(exec_client, filename, content, message):
    response = _post(exec_client, content, filename)
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_a_zip_that_is_not_a_workbook_is_refused(exec_client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")
    response = _post(exec_client, buffer.getvalue(), "sneaky.xlsx")
    assert response.status_code == 422


def test_unknown_source_is_refused(exec_client):
    response = _post(exec_client, (HEADER + GOOD_ROW).encode(), source_id=99999)
    assert response.status_code == 422


def test_rejected_rows_download_explains_each_row(exec_client):
    bad = 'ORD-M-80002,not a date,1199.00,180,30,90,0,5.71,1.20,0,892.09,=HYPERLINK("x")\n'
    rows = GOOD_ROW + "".join(
        GOOD_ROW.replace("U1", f"U{i}").replace("80001", f"8{i:04d}") for i in range(2, 30)
    )
    upload_id = _post(exec_client, (HEADER + rows + bad).encode()).json()["id"]
    worker.run_one()

    response = exec_client.get(f"/api/uploads/{upload_id}/errors.csv")
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith('m1-rejected-rows.csv"')
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("Row,Problem,Order ID")
    assert "Settlement date: “not a date” isn't a valid date" in lines[1]
    assert "'=HYPERLINK" in lines[1]  # a formula from a partner file stays text


def test_sources_list_shows_the_active_mapping(exec_client):
    listed = exec_client.get("/api/sources").json()
    assert len(listed) == 11
    assert all(s["mapping_version"] == 1 for s in listed)


@pytest.mark.parametrize(
    ("value", "safe"),
    [
        ("=1+1", "'=1+1"),
        ("+91 5000", "'+91 5000"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("-180.00", "-180.00"),
        ("ORD-M-1", "ORD-M-1"),
        (None, ""),
    ],
)
def test_safe_cell(value, safe):
    assert safe_cell(value) == safe
