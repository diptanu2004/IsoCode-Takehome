import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Connection

from app.auth.deps import require
from app.auth.permissions import Permission
from app.auth.sessions import CurrentUser
from app.db import get_conn
from app.ingest.apply import MappingError
from app.mapping import service
from app.mapping.profile import DATE_FORMATS, DATETIME_FORMATS

router = APIRouter(prefix="/api", tags=["mapping"])
_can_approve = require(Permission.MAPPING_APPROVE)
_can_view = require(Permission.RECON_VIEW)


class FieldSpec(BaseModel):
    """One field's column, plus reading options. Send only `column` to have options inferred."""

    column: str | None = Field(default=None, max_length=200)
    negate: bool | None = None
    date_format: str | None = None
    scale: int | None = Field(default=None, ge=1, le=1_000_000)
    values: dict[str, str] | None = None
    strip_suffix: str | None = Field(default=None, max_length=40)

    @field_validator("date_format")
    @classmethod
    def known_date_format(cls, value: str | None) -> str | None:
        if value is not None and value not in DATE_FORMATS | DATETIME_FORMATS:
            raise ValueError("Unknown date format.")
        return value

    @field_validator("strip_suffix")
    @classmethod
    def valid_pattern(cls, value: str | None) -> str | None:
        if value:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError("The ID suffix pattern isn't valid.") from exc
        return value


class MappingRequest(BaseModel):
    fields: dict[str, FieldSpec]
    personal_data_columns: list[str] = Field(default_factory=list, max_length=200)
    provenance: dict[str, Literal["saved", "kept", "rules", "ai", "both", "manual"]] = Field(
        default_factory=dict
    )

    def as_request(self) -> dict:
        return {
            "fields": {k: v.model_dump(exclude_unset=True) for k, v in self.fields.items()},
            "personal_data_columns": self.personal_data_columns,
            "provenance": self.provenance,
        }


class VersionOut(BaseModel):
    id: int
    version: int
    is_active: bool
    suggested_by: str
    approved_by: str | None
    approved_at: datetime | None
    uploads: int
    columns: dict[str, str]
    personal_data_columns: list[str]


def _call(fn, *args):
    try:
        return fn(*args)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except service.MappingConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except MappingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/uploads/{upload_id}/mapping", dependencies=[Depends(_can_view)])
def get_mapping_review(
    upload_id: int, refresh: bool = False, conn: Connection = Depends(get_conn, scope="function")
) -> dict:
    """Detected columns, target fields and the suggested mapping for a file in a new format."""
    return _call(service.review, conn, upload_id, refresh)


@router.post("/uploads/{upload_id}/mapping/preview", dependencies=[Depends(_can_approve)])
def preview_mapping(
    upload_id: int, body: MappingRequest, conn: Connection = Depends(get_conn, scope="function")
) -> dict:
    """The first 20 rows as they would load, problems per cell, and what blocks approval."""
    return _call(service.preview, conn, upload_id, body.as_request())


@router.post("/uploads/{upload_id}/mapping")
def approve_mapping(
    upload_id: int,
    body: MappingRequest,
    user: CurrentUser = Depends(_can_approve),
    conn: Connection = Depends(get_conn, scope="function"),
) -> dict:
    """Save the mapping as a new version and queue the file (and any like it) to load."""
    return _call(service.approve, conn, upload_id, body.as_request(), user.id)


@router.get("/sources/{source_id}/mappings", dependencies=[Depends(_can_view)])
def list_mapping_versions(
    source_id: int, conn: Connection = Depends(get_conn, scope="function")
) -> list[VersionOut]:
    return [VersionOut(**v) for v in service.versions(conn, source_id)]


@router.post("/sources/{source_id}/mappings/{version_id}/activate")
def activate_mapping_version(
    source_id: int,
    version_id: int,
    user: CurrentUser = Depends(_can_approve),
    conn: Connection = Depends(get_conn, scope="function"),
) -> dict:
    """Make a saved version the one used for new files (rollback)."""
    return _call(service.activate, conn, source_id, version_id, user.id)
