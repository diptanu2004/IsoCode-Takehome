"""Review, preview, approve and roll back column mappings (TDD §7.1 step 4, §9)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

import polars as pl
from sqlalchemy import Connection, func, select, update

from app import audit
from app.enums import FileKind, MappingSource, UploadStatus
from app.ingest.apply import ERROR, ROW_NO, MappingError, apply_mapping, cell_problem_column
from app.ingest.pipeline import MAX_ERROR_SHARE, upload_path
from app.ingest.reader import RawTable, read_table
from app.mapping.fields import FIELDS
from app.mapping.fingerprint import header_fingerprint
from app.mapping.llm import is_configured
from app.mapping.profile import profile_table
from app.mapping.rules import infer_options
from app.mapping.suggest import SavedVersion, build_suggestion
from app.schema import mapping_versions, partners, sources, uploads, users

PREVIEW_ROWS = 20  # from the brief
_OPTION_KEYS = ("negate", "date_format", "scale", "values", "strip_suffix")


class MappingConflict(ValueError):
    """The upload isn't waiting for a mapping (any more). Shown to the user."""


@dataclass
class Context:
    upload_id: int
    filename: str
    source_id: int
    source: str
    partner: str
    kind: FileKind
    detected: dict
    table: RawTable


def _context(conn: Connection, upload_id: int) -> Context:
    row = conn.execute(
        select(
            uploads.c.id,
            uploads.c.filename,
            uploads.c.status,
            uploads.c.detected,
            sources.c.id.label("source_id"),
            sources.c.name.label("source"),
            sources.c.file_kind,
            partners.c.name.label("partner"),
        )
        .join(sources, sources.c.id == uploads.c.source_id)
        .join(partners, partners.c.id == sources.c.partner_id)
        .where(uploads.c.id == upload_id)
    ).one_or_none()
    if row is None:
        raise LookupError("Upload not found.")
    if row.status != UploadStatus.NEEDS_MAPPING:
        raise MappingConflict("This upload isn't waiting for a column mapping.")
    try:
        table = read_table(upload_path(upload_id, row.filename))
    except FileNotFoundError as exc:
        raise MappingConflict("The file is no longer on the server. Upload it again.") from exc
    return Context(
        row.id,
        row.filename,
        row.source_id,
        row.source,
        row.partner,
        FileKind(row.file_kind),
        row.detected or {},
        table,
    )


def _saved_versions(conn: Connection, source_id: int) -> list[SavedVersion]:
    rows = conn.execute(
        select(mapping_versions).where(mapping_versions.c.source_id == source_id)
    ).all()
    return [
        SavedVersion(
            r.version, r.header_fingerprint, r.mapping, list(r.personal_data_columns), r.is_active
        )
        for r in rows
    ]


def review(conn: Connection, upload_id: int, refresh: bool = False) -> dict:
    """Everything the mapping screen needs. The suggestion is computed once and kept, so
    reopening the screen doesn't call the AI again."""
    ctx = _context(conn, upload_id)
    profiles = profile_table(ctx.table)
    suggestion = ctx.detected.get("suggestion")
    if suggestion is None or refresh:
        built = build_suggestion(
            ctx.kind,
            ctx.partner,
            profiles,
            header_fingerprint(ctx.table.headers),
            _saved_versions(conn, ctx.source_id),
            use_ai=is_configured(),
        )
        suggestion = built.to_dict()
        conn.execute(
            update(uploads)
            .where(uploads.c.id == upload_id)
            .values(detected={**ctx.detected, "suggestion": suggestion})
        )
    active = conn.execute(
        select(mapping_versions.c.version, mapping_versions.c.mapping).where(
            mapping_versions.c.source_id == ctx.source_id, mapping_versions.c.is_active
        )
    ).one_or_none()
    return {
        "upload": {
            "id": ctx.upload_id,
            "filename": ctx.filename,
            "header_row": ctx.table.header_row,
            "rows": ctx.table.data.height,
        },
        "source": {
            "id": ctx.source_id,
            "name": ctx.source,
            "partner": ctx.partner,
            "file_kind": ctx.kind,
        },
        "active_version": active.version if active else None,
        "active_columns": sorted({spec["column"] for spec in active.mapping["fields"].values()})
        if active
        else [],
        "columns": [
            {
                "header": p.header,
                "kind": p.kind,
                "detail": p.detail,
                "personal": p.personal,
                "categories": p.categories,
            }
            for p in profiles
        ],
        "fields": [
            {
                "name": f.name,
                "label": f.label,
                "description": f.description,
                "type": f.type.value,
                "required": f.required and not f.key_group,
                "key_group": f.key_group,
                "deduction": f.deduction,
                "choices": [c.value for c in f.choices] if f.choices else [],
            }
            for f in FIELDS[ctx.kind]
        ],
        "suggestion": suggestion,
    }


def _resolve(ctx: Context, requested: dict) -> dict:
    """Fill in options the client left out (it sends just a column after a change)."""
    profiles = {p.header: p for p in profile_table(ctx.table)}
    previous = (ctx.detected.get("suggestion") or {}).get("fields", {})
    resolved: dict = {}
    for f in FIELDS[ctx.kind]:
        spec = requested.get(f.name) or {}
        column = spec.get("column")
        if not column:
            continue
        if column not in profiles:
            raise MappingError(f"Column “{column}” isn't in this file.")
        if any(key in spec for key in _OPTION_KEYS):
            options = {k: spec[k] for k in _OPTION_KEYS if spec.get(k) not in (None, False, {})}
        else:
            options = infer_options(f, profiles[column], previous.get(f.name, {}).get("options"))
        resolved[f.name] = {"column": column, **options}
    return resolved


def _jsonable(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _blocking(kind: FileKind, fields: dict, total: int, rejected: int) -> list[str]:
    issues = []
    used: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for f in FIELDS[kind]:
        column = fields.get(f.name, {}).get("column")
        if f.key_group:
            groups.setdefault(f.key_group, []).append(f.name if column else "")
        elif f.required and not column:
            issues.append(f"{f.label} needs a column.")
        if column:
            if column in used:
                issues.append(f"“{column}” is used for both {used[column]} and {f.label}.")
            used[column] = f.label
    for group, names in groups.items():
        if not any(names):
            labels = " or ".join(f.label for f in FIELDS[kind] if f.key_group == group)
            issues.append(f"Map at least one of: {labels}.")
    if total and rejected / total > MAX_ERROR_SHARE:
        issues.append(
            f"{rejected:,} of {total:,} rows ({rejected / total:.0%}) can't be read with this "
            "mapping. Check the highlighted cells."
        )
    return issues


def preview(conn: Connection, upload_id: int, requested: dict) -> dict:
    ctx = _context(conn, upload_id)
    fields = _resolve(ctx, requested.get("fields", {}))
    mapping = {"header_row": ctx.table.header_row, "sheet": None, "fields": fields}
    blocking: list[str] = []
    rows: list[dict] = []
    total = rejected = 0
    examples: list[dict] = []
    try:
        mapped = apply_mapping(ctx.table, ctx.kind, mapping, cell_problems=True, strict=False)
    except MappingError as exc:
        blocking.append(str(exc))
        mapped = None
    if mapped is not None:
        total = mapped.height
        bad = mapped.filter(pl.col(ERROR).is_not_null())
        rejected = bad.height
        examples = [
            {"row": r[ROW_NO], "problem": r[ERROR]}
            for r in bad.head(5).select(ROW_NO, ERROR).to_dicts()
        ]
        for record in mapped.head(PREVIEW_ROWS).to_dicts():
            rows.append(
                {
                    "row": record[ROW_NO],
                    "values": {name: _jsonable(record.get(name)) for name in fields},
                    "problems": {
                        name: record[cell_problem_column(name)]
                        for name in fields
                        if record.get(cell_problem_column(name))
                    },
                }
            )
    blocking = blocking or _blocking(ctx.kind, fields, total, rejected)
    return {
        "mapping": mapping,
        "rows": rows,
        "total_rows": total,
        "rejected_rows": rejected,
        "examples": examples,
        "blocking": blocking,
    }


def _provenance_label(provenance: dict) -> MappingSource:
    sources_used = set(provenance.values())
    if "manual" in sources_used:
        return MappingSource.MANUAL
    if sources_used & {"ai", "both"}:
        return MappingSource.AI
    return MappingSource.RULES


def approve(conn: Connection, upload_id: int, requested: dict, user_id: int) -> dict:
    result = preview(conn, upload_id, requested)
    if result["blocking"]:
        raise MappingError(" ".join(result["blocking"]))
    ctx = _context(conn, upload_id)
    fingerprint = header_fingerprint(ctx.table.headers)
    personal = [h for h in requested.get("personal_data_columns", []) if h in ctx.table.headers]
    provenance = {
        k: v
        for k, v in (requested.get("provenance") or {}).items()
        if k in result["mapping"]["fields"]
    }

    previous = conn.execute(
        select(mapping_versions.c.id, mapping_versions.c.version).where(
            mapping_versions.c.source_id == ctx.source_id, mapping_versions.c.is_active
        )
    ).one_or_none()
    next_version = (
        conn.execute(
            select(func.max(mapping_versions.c.version)).where(
                mapping_versions.c.source_id == ctx.source_id
            )
        ).scalar()
        or 0
    ) + 1
    conn.execute(
        update(mapping_versions)
        .where(mapping_versions.c.source_id == ctx.source_id)
        .values(is_active=False)
    )
    version_id = conn.execute(
        mapping_versions.insert()
        .values(
            source_id=ctx.source_id,
            version=next_version,
            header_fingerprint=fingerprint,
            mapping={**result["mapping"], "provenance": provenance},
            personal_data_columns=personal,
            suggested_by=_provenance_label(provenance).value,
            approved_by=user_id,
            approved_at=datetime.now(UTC),
            is_active=True,
        )
        .returning(mapping_versions.c.id)
    ).scalar_one()

    # This file, and any other waiting file with the same columns, can now load.
    requeued = (
        conn.execute(
            update(uploads)
            .where(
                uploads.c.source_id == ctx.source_id,
                uploads.c.status == UploadStatus.NEEDS_MAPPING,
                uploads.c.detected["fingerprint"].astext == fingerprint,
            )
            .values(status=UploadStatus.QUEUED.value, error_message=None)
            .returning(uploads.c.id)
        )
        .scalars()
        .all()
    )
    audit.record(
        conn,
        user_id,
        "mapping.approve",
        "source",
        ctx.source_id,
        before={"version": previous.version} if previous else None,
        after={
            "version": next_version,
            "upload_id": upload_id,
            "provenance": provenance,
            "personal_data_columns": personal,
        },
    )
    return {"version": next_version, "version_id": version_id, "requeued_uploads": requeued}


def versions(conn: Connection, source_id: int) -> list[dict]:
    rows = conn.execute(
        select(
            mapping_versions.c.id,
            mapping_versions.c.version,
            mapping_versions.c.mapping,
            mapping_versions.c.suggested_by,
            mapping_versions.c.approved_at,
            mapping_versions.c.is_active,
            mapping_versions.c.personal_data_columns,
            users.c.name.label("approved_by"),
            select(func.count())
            .where(uploads.c.mapping_version_id == mapping_versions.c.id)
            .scalar_subquery()
            .label("uploads"),
        )
        .outerjoin(users, users.c.id == mapping_versions.c.approved_by)
        .where(mapping_versions.c.source_id == source_id)
        .order_by(mapping_versions.c.version.desc())
    ).all()
    return [
        {
            "id": r.id,
            "version": r.version,
            "is_active": r.is_active,
            "suggested_by": r.suggested_by,
            "approved_by": r.approved_by,
            "approved_at": r.approved_at,
            "uploads": r.uploads,
            "columns": {k: v["column"] for k, v in r.mapping["fields"].items()},
            "personal_data_columns": list(r.personal_data_columns),
        }
        for r in rows
    ]


def activate(conn: Connection, source_id: int, version_id: int, user_id: int) -> dict:
    """Roll back (or forward) to a saved version for the files that come next."""
    target = conn.execute(
        select(
            mapping_versions.c.version,
            mapping_versions.c.is_active,
            mapping_versions.c.header_fingerprint,
        ).where(mapping_versions.c.id == version_id, mapping_versions.c.source_id == source_id)
    ).one_or_none()
    if target is None:
        raise LookupError("Mapping version not found.")
    previous = conn.execute(
        select(mapping_versions.c.version).where(
            mapping_versions.c.source_id == source_id, mapping_versions.c.is_active
        )
    ).scalar()
    if not target.is_active:
        # Two statements: the one-active-version index is checked row by row.
        conn.execute(
            update(mapping_versions)
            .where(mapping_versions.c.source_id == source_id)
            .values(is_active=False)
        )
        conn.execute(
            update(mapping_versions)
            .where(mapping_versions.c.id == version_id)
            .values(is_active=True)
        )
        audit.record(
            conn,
            user_id,
            "mapping.activate",
            "source",
            source_id,
            before={"version": previous},
            after={"version": target.version},
        )
    requeued = (
        conn.execute(
            update(uploads)
            .where(
                uploads.c.source_id == source_id,
                uploads.c.status == UploadStatus.NEEDS_MAPPING,
                uploads.c.detected["fingerprint"].astext == target.header_fingerprint,
            )
            .values(status=UploadStatus.QUEUED.value)
            .returning(uploads.c.id)
        )
        .scalars()
        .all()
    )
    return {"active_version": target.version, "requeued_uploads": requeued}
