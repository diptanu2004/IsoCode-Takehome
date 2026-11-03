"""Turn partner text cells into clean values, a whole column at a time (TDD §8.7).

Each function takes the raw text column and the field's mapping options and returns an expression
for the clean value; an unreadable cell becomes null, which apply_mapping reports as a row error.
"""

import polars as pl

from app.mapping.fields import FieldType

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%d.%m.%Y")
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%dT%H:%M:%S",
)
_EXCEL_EPOCH = pl.date(1899, 12, 30)
LOCAL_TZ = "Asia/Kolkata"


def _text(raw: pl.Expr) -> pl.Expr:
    stripped = raw.str.strip_chars()
    return pl.when(stripped == "").then(None).otherwise(stripped)


def _number(raw: pl.Expr) -> pl.Expr:
    """1,199.00 / ₹1,199 / Rs. 1199 / (180.00) / -180 → float. Brackets mean negative."""
    text = _text(raw)
    bracketed = text.str.starts_with("(") & text.str.ends_with(")")
    digits = text.str.replace_all(r"(?i)rs\.?|inr|₹|,|\s|\(|\)", "")
    value = digits.cast(pl.Float64, strict=False)
    return pl.when(bracketed).then(-value.abs()).otherwise(value)


def money(raw: pl.Expr, negate: bool = False) -> pl.Expr:
    paise = (_number(raw) * 100).round(0).cast(pl.Int64)
    return -paise if negate else paise


def integer(raw: pl.Expr, scale: float | None = None) -> pl.Expr:
    return (_number(raw) * (scale or 1)).round(0).cast(pl.Int64)


def identifier(raw: pl.Expr, strip_suffix: str | None = None) -> pl.Expr:
    """Trim and upper-case; repair IDs Excel turned into 4013631710.0 or 4.01363171E+09."""
    text = _text(raw).str.to_uppercase()
    as_float = text.cast(pl.Float64, strict=False)
    repaired = (
        pl.when(text.str.contains(r"^\d+\.0+$"))
        .then(text.str.replace(r"\.0+$", ""))
        .when(text.str.contains(r"^\d(\.\d+)?E\+\d+$"))
        .then(as_float.cast(pl.Int64, strict=False).cast(pl.String))
        .otherwise(text)
    )
    if strip_suffix:
        repaired = repaired.str.replace(strip_suffix, "")
    return repaired


def _parse_all(text: pl.Expr, dtype, formats: tuple[str, ...]) -> pl.Expr:
    return pl.coalesce([text.str.strptime(dtype, f, strict=False) for f in formats])


def date(raw: pl.Expr, date_format: str | None = None) -> pl.Expr:
    text = _text(raw)
    if date_format:
        return text.str.strptime(pl.Date, date_format, strict=False)
    serial = text.cast(pl.Float64, strict=False)
    from_serial = pl.when(serial.is_between(20000, 80000)).then(
        _EXCEL_EPOCH + pl.duration(days=serial.floor().cast(pl.Int64))
    )
    with_time = _parse_all(text, pl.Datetime("us"), _DATETIME_FORMATS).dt.date()
    return pl.coalesce([_parse_all(text, pl.Date, _DATE_FORMATS), with_time, from_serial])


def datetime(raw: pl.Expr, date_format: str | None = None) -> pl.Expr:
    """Local (IST) timestamps without a zone; a bare date counts as midnight."""
    text = _text(raw)
    formats = (date_format,) if date_format else _DATETIME_FORMATS
    parsed = pl.coalesce(
        [
            _parse_all(text, pl.Datetime("us"), formats),
            date(raw).cast(pl.Datetime("us")),
        ]
    )
    return parsed.dt.replace_time_zone(LOCAL_TZ)


def choice(raw: pl.Expr, values: dict[str, str] | None, allowed: list[str]) -> pl.Expr:
    """Map partner labels (case-insensitive) to stored values; allowed values pass through."""
    lookup = {v.lower(): v for v in allowed}
    lookup |= {k.strip().lower(): v for k, v in (values or {}).items()}
    return (
        _text(raw).str.to_lowercase().replace_strict(lookup, default=None, return_dtype=pl.String)
    )


def clean(field_type: FieldType, raw: pl.Expr, spec: dict, allowed: list[str]) -> pl.Expr:
    match field_type:
        case FieldType.MONEY:
            return money(raw, spec.get("negate", False))
        case FieldType.INTEGER | FieldType.WEIGHT:
            return integer(raw, spec.get("scale"))
        case FieldType.ID:
            return identifier(raw, spec.get("strip_suffix"))
        case FieldType.DATE:
            return date(raw, spec.get("date_format"))
        case FieldType.DATETIME:
            return datetime(raw, spec.get("date_format"))
        case FieldType.ENUM:
            return choice(raw, spec.get("values"), allowed)
        case _:
            return _text(raw)
