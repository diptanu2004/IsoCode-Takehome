"""Apply a saved column mapping to a raw table: clean every mapped field and explain bad rows."""

import polars as pl

from app.enums import FileKind
from app.ingest.normalise import clean
from app.ingest.reader import RawTable
from app.mapping.fields import FIELDS, FieldType
from app.mapping.fingerprint import normalise_header

ROW_NO = "_row_no"
ERROR = "_error"

_TYPE_WORDS = {
    FieldType.MONEY: "amount",
    FieldType.INTEGER: "whole number",
    FieldType.WEIGHT: "weight",
    FieldType.DATE: "date",
    FieldType.DATETIME: "date and time",
    FieldType.ENUM: "value",
    FieldType.ID: "ID",
    FieldType.TEXT: "value",
}


class MappingError(ValueError):
    """The mapping doesn't fit this file. The message is shown to the user."""


def _present(raw: pl.Expr) -> pl.Expr:
    return raw.str.strip_chars().fill_null("") != ""


def drop_empty_rows(table: RawTable) -> pl.DataFrame:
    """Keep each row's file row number, and drop rows with nothing in them."""
    data = table.data.with_row_index(ROW_NO, offset=table.first_row_no)
    if not table.headers:
        return data
    return data.filter(pl.any_horizontal(_present(pl.col(h)) for h in table.headers))


def cell_problem_column(field_name: str) -> str:
    return f"_problem:{field_name}"


def apply_mapping(
    table: RawTable,
    kind: FileKind,
    mapping: dict,
    cell_problems: bool = False,
    strict: bool = True,
) -> pl.DataFrame:
    """Columns: _row_no, one per mapped field (clean values), _error (null when the row is fine).

    With cell_problems, also one column per field holding that cell's problem, for the preview.
    strict=False lets a preview run before every required field is mapped.
    """
    data = drop_empty_rows(table)
    by_header = {normalise_header(h): h for h in table.headers}
    specs: dict = mapping.get("fields", {})

    values: list[pl.Expr] = []
    errors: list[pl.Expr] = []
    problems: list[pl.Expr] = []
    key_groups: dict[str, list[pl.Expr]] = {}
    for f in FIELDS[kind]:
        spec = specs.get(f.name)
        if not spec:
            if strict and f.required and not f.key_group:
                raise MappingError(f"{f.label} isn't mapped to a column.")
            continue
        header = by_header.get(normalise_header(spec["column"]))
        if header is None:
            raise MappingError(f"Column “{spec['column']}” for {f.label} isn't in this file.")

        raw = pl.col(header)
        allowed = [c.value for c in f.choices] if f.choices else []
        value = clean(f.type, raw, spec, allowed)
        values.append(value.alias(f.name))

        present = _present(raw)
        shown = raw.str.strip_chars().str.slice(0, 40)
        invalid = pl.when(present & value.is_null()).then(
            pl.format(
                "{}: “{}” isn't a valid {}", pl.lit(f.label), shown, pl.lit(_TYPE_WORDS[f.type])
            )
        )
        missing = pl.lit(None, pl.String)
        if f.key_group:
            key_groups.setdefault(f.key_group, []).append(present)
        elif f.required and not f.blank_ok:
            missing = pl.when(~present).then(pl.lit(f"{f.label} is missing"))
        errors += [invalid, missing]
        problems.append(pl.coalesce(invalid, missing).alias(cell_problem_column(f.name)))

    for group, present in key_groups.items():
        labels = " or ".join(f.label for f in FIELDS[kind] if f.key_group == group)
        errors.append(pl.when(~pl.any_horizontal(present)).then(pl.lit(f"Needs an {labels}")))

    error = pl.coalesce(errors) if errors else pl.lit(None, pl.String)
    return data.select(
        pl.col(ROW_NO), *values, error.alias(ERROR), *(problems if cell_problems else [])
    )
