import io
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import Connection, func, select

from app.auth.deps import require
from app.auth.permissions import Permission
from app.auth.sessions import CurrentUser
from app.config import get_settings
from app.db import get_conn
from app.enums import FileKind, UploadStatus
from app.exports import to_csv
from app.ingest.pipeline import UploadRejected, register_upload
from app.schema import mapping_versions, partners, source_rows, sources, uploads, users

router = APIRouter(prefix="/api", tags=["uploads"])

_ALLOWED = {".csv", ".xlsx"}
_CHUNK = 1024 * 1024
_MAX_UNZIPPED = 500 * 1024 * 1024  # an .xlsx is a zip; refuse decompression bombs
_can_upload = require(Permission.UPLOAD_CREATE)


class SourceOut(BaseModel):
    id: int
    name: str
    partner: str
    file_kind: FileKind
    mapping_version: int | None
    last_upload_at: datetime | None


class UploadOut(BaseModel):
    id: int
    source_id: int
    source: str
    partner: str
    file_kind: FileKind
    filename: str
    size_bytes: int
    status: UploadStatus
    row_count: int | None
    error_count: int | None
    error_message: str | None
    stats: dict
    stage_timings: dict
    mapping_version: int | None
    uploaded_by: str | None
    created_at: datetime
    finished_at: datetime | None


def _upload_query():
    return (
        select(
            uploads.c.id,
            uploads.c.source_id,
            sources.c.name.label("source"),
            partners.c.name.label("partner"),
            sources.c.file_kind,
            uploads.c.filename,
            uploads.c.size_bytes,
            uploads.c.status,
            uploads.c.row_count,
            uploads.c.error_count,
            uploads.c.error_message,
            uploads.c.stats,
            uploads.c.stage_timings,
            mapping_versions.c.version.label("mapping_version"),
            users.c.name.label("uploaded_by"),
            uploads.c.created_at,
            uploads.c.finished_at,
        )
        .join(sources, sources.c.id == uploads.c.source_id)
        .join(partners, partners.c.id == sources.c.partner_id)
        .outerjoin(mapping_versions, mapping_versions.c.id == uploads.c.mapping_version_id)
        .outerjoin(users, users.c.id == uploads.c.uploaded_by)
    )


def _get_upload(conn: Connection, upload_id: int) -> UploadOut:
    row = conn.execute(_upload_query().where(uploads.c.id == upload_id)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found.")
    return UploadOut.model_validate(row, from_attributes=True)


@router.get("/sources", dependencies=[Depends(require(Permission.RECON_VIEW))])
def list_sources(conn: Connection = Depends(get_conn, scope="function")) -> list[SourceOut]:
    last_upload = (
        select(uploads.c.source_id, func.max(uploads.c.created_at).label("at"))
        .group_by(uploads.c.source_id)
        .subquery()
    )
    rows = conn.execute(
        select(
            sources.c.id,
            sources.c.name,
            partners.c.name.label("partner"),
            sources.c.file_kind,
            mapping_versions.c.version.label("mapping_version"),
            last_upload.c.at.label("last_upload_at"),
        )
        .join(partners, partners.c.id == sources.c.partner_id)
        .outerjoin(
            mapping_versions,
            (mapping_versions.c.source_id == sources.c.id) & mapping_versions.c.is_active,
        )
        .outerjoin(last_upload, last_upload.c.source_id == sources.c.id)
        .order_by(sources.c.id)
    ).all()
    return [SourceOut.model_validate(r, from_attributes=True) for r in rows]


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    chunks, size = [], 0
    while chunk := await file.read(_CHUNK):
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"The file is larger than {limit // _CHUNK} MB. Split it, or ask for the limit "
                "to be raised.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _check_content(suffix: str, content: bytes) -> None:
    """Check the bytes really are what the extension says, before anything parses them."""
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty.")
    if suffix == ".xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = archive.namelist()
                unzipped = sum(i.file_size for i in archive.infolist())
        except zipfile.BadZipFile:
            names, unzipped = [], 0
        if "xl/workbook.xml" not in names:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "This isn't a valid .xlsx file. Open it in Excel and save it as .xlsx or .csv.",
            )
        if unzipped > _MAX_UNZIPPED:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "This workbook is too large to read."
            )
    elif b"\x00" in content[: 64 * 1024]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This doesn't look like a CSV file. Save it as .csv or .xlsx and upload again.",
        )


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def create_upload(
    source_id: int = Form(),
    file: UploadFile = File(),
    user: CurrentUser = Depends(_can_upload),
    conn: Connection = Depends(get_conn, scope="function"),
) -> UploadOut:
    filename = Path(file.filename or "upload").name[:200]
    suffix = Path(filename).suffix.lower()
    if suffix == ".xls":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Old .xls files aren't supported. Open it in Excel and save it as .xlsx or .csv.",
        )
    if suffix not in _ALLOWED:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Upload a .csv or .xlsx file.")
    if conn.execute(select(sources.c.id).where(sources.c.id == source_id)).first() is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a known report source.")

    content = await _read_limited(file, get_settings().max_upload_mb * _CHUNK)
    _check_content(suffix, content)
    try:
        upload_id = register_upload(conn, source_id, filename, content, user.id)
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _get_upload(conn, upload_id)


@router.get("/uploads", dependencies=[Depends(require(Permission.RECON_VIEW))])
def list_uploads(
    limit: int = 100, conn: Connection = Depends(get_conn, scope="function")
) -> list[UploadOut]:
    limit = max(1, min(limit, 500))
    rows = conn.execute(_upload_query().order_by(uploads.c.created_at.desc()).limit(limit)).all()
    return [UploadOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/uploads/{upload_id}", dependencies=[Depends(require(Permission.RECON_VIEW))])
def get_upload(upload_id: int, conn: Connection = Depends(get_conn, scope="function")) -> UploadOut:
    return _get_upload(conn, upload_id)


@router.get(
    "/uploads/{upload_id}/errors.csv", dependencies=[Depends(require(Permission.RECON_VIEW))]
)
def rejected_rows(
    upload_id: int, conn: Connection = Depends(get_conn, scope="function")
) -> Response:
    upload = _get_upload(conn, upload_id)
    rows = conn.execute(
        select(source_rows.c.row_no, source_rows.c.error, source_rows.c.raw)
        .where(source_rows.c.upload_id == upload_id, source_rows.c.error.is_not(None))
        .order_by(source_rows.c.row_no)
    ).all()
    # jsonb doesn't keep key order, so put the columns back in the file's own order.
    detected = conn.execute(select(uploads.c.detected).where(uploads.c.id == upload_id)).scalar()
    stored = set(rows[0].raw) if rows else set()
    columns = [h for h in (detected or {}).get("headers", []) if h in stored]
    body = to_csv(
        ["Row", "Problem", *columns],
        ([r.row_no, r.error, *(r.raw.get(c) for c in columns)] for r in rows),
    )
    name = Path(upload.filename).stem
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}-rejected-rows.csv"'},
    )
