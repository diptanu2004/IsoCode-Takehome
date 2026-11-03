"""Describe each column of a file by the shape of its values, never the values themselves.

The description ("date like 03-Nov-2026 → dd-Mon-yyyy", "amount, all positive", "ID, 99% unique,
shape AAA-A-99999") is what the AI sees, so no cell value ever leaves the system (TDD §9.1, §12.2).
It also drives the rule-based suggestions and the options inferred for a chosen column.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

import polars as pl

from app.ingest.reader import RawTable

SAMPLE_ROWS = 1000
CATEGORY_LIMIT = 12

DATE_FORMATS = {
    "%Y-%m-%d": "yyyy-mm-dd",
    "%d/%m/%Y": "dd/mm/yyyy",
    "%d-%m-%Y": "dd-mm-yyyy",
    "%d-%b-%Y": "dd-Mon-yyyy",
    "%d %b %Y": "dd Mon yyyy",
    "%d.%m.%Y": "dd.mm.yyyy",
    "%m/%d/%Y": "mm/dd/yyyy",
}
DATETIME_FORMATS = {
    "%Y-%m-%d %H:%M:%S": "yyyy-mm-dd hh:mm:ss",
    "%Y-%m-%d %H:%M": "yyyy-mm-dd hh:mm",
    "%d/%m/%Y %H:%M": "dd/mm/yyyy hh:mm",
    "%d/%m/%Y %H:%M:%S": "dd/mm/yyyy hh:mm:ss",
}

_AMOUNT = re.compile(r"^\(?-?\s*(₹|rs\.?|inr)?\s*-?[\d,]+(\.\d+)?\)?$", re.I)
_PHONE = re.compile(r"^(\+?91[\s-]?)?[5-9]\d{9}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
_PERSONAL_HEADER = re.compile(
    r"\b(name|customer|buyer|recipient|consignee|phone|mobile|contact|email|e-mail|address)\b", re.I
)
# Headers that name a business identifier, not a person, even if they contain "name" or "no".
_BUSINESS_HEADER = re.compile(
    r"\b(order|awb|waybill|sku|product|item|courier|partner|channel|seller|batch|invoice|utr)\b",
    re.I,
)


@dataclass
class ColumnProfile:
    header: str
    kind: str  # empty | date | datetime | amount | integer | id | category | text
    detail: str  # what the AI and the mapping screen are told about the column
    filled: float  # share of rows with a value
    distinct: int
    date_format: str | None = None
    negative_share: float = 0.0
    has_decimals: bool = False
    categories: list[str] = field(default_factory=list)  # only for short category lists
    personal: str | None = None  # why the column looks like personal data

    def for_ai(self) -> dict:
        """What the AI is shown: the description, never values (categories are left out too)."""
        keep = ("header", "kind", "detail", "filled", "distinct")
        return {k: v for k, v in asdict(self).items() if k in keep}


def _shape(value: str) -> str:
    """ORD-M-88120 → AAA-A-99999: the layout of an ID without its value."""
    return re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", value))[:24]


def _parse_share(values: list[str], fmt: str) -> float:
    ok = 0
    for v in values:
        try:
            datetime.strptime(v, fmt)
            ok += 1
        except ValueError:
            pass
    return ok / len(values)


def _date_format(values: list[str], formats: dict[str, str]) -> str | None:
    scores = {fmt: _parse_share(values[:200], fmt) for fmt in formats}
    best = max(scores, key=scores.get)
    if scores[best] < 0.9:
        return None
    # dd/mm and mm/dd both fit days ≤ 12; India uses dd/mm, so prefer it on a tie.
    if best == "%m/%d/%Y" and scores.get("%d/%m/%Y", 0) >= scores[best]:
        best = "%d/%m/%Y"
    return best


def _to_float(value: str) -> float | None:
    text = re.sub(r"(?i)rs\.?|inr|₹|,|\s", "", value)
    negative = text.startswith("(") and text.endswith(")")
    try:
        number = float(text.strip("()"))
    except ValueError:
        return None
    return -abs(number) if negative else number


def _personal(header: str, values: list[str]) -> str | None:
    if _BUSINESS_HEADER.search(header):
        return None
    if _PERSONAL_HEADER.search(header):
        return "the column name suggests customer details"
    if values:
        phones = sum(1 for v in values if _PHONE.match(v.replace(" ", ""))) / len(values)
        if phones >= 0.8:
            return "the values look like phone numbers"
        if sum(1 for v in values if _EMAIL.match(v)) / len(values) >= 0.8:
            return "the values look like email addresses"
    return None


def profile_column(header: str, series: pl.Series) -> ColumnProfile:
    total = max(series.len(), 1)
    values = [v.strip() for v in series.drop_nulls().to_list() if v and v.strip()]
    filled = round(len(values) / total, 3)
    distinct = len(set(values))
    personal = _personal(header, values)
    base = {"header": header, "filled": filled, "distinct": distinct, "personal": personal}
    if not values:
        return ColumnProfile(kind="empty", detail="empty column", **base)

    if fmt := _date_format(values, DATETIME_FORMATS):
        return ColumnProfile(
            kind="datetime",
            detail=f"date and time, {DATETIME_FORMATS[fmt]}",
            date_format=fmt,
            **base,
        )
    if fmt := _date_format(values, DATE_FORMATS):
        return ColumnProfile(
            kind="date", detail=f"date, {DATE_FORMATS[fmt]}", date_format=fmt, **base
        )

    numbers = [n for n in (_to_float(v) for v in values if _AMOUNT.match(v)) if n is not None]
    unique_share = distinct / len(values)
    if len(numbers) >= 0.95 * len(values):
        negative = round(sum(1 for n in numbers if n < 0) / len(numbers), 3)
        decimals = any("." in v for v in values)
        money_marks = any(re.search(r"[₹,()]|rs|inr", v, re.I) for v in values)
        long_integers = not decimals and min(len(v) for v in values) >= 7
        if long_integers and unique_share > 0.9:
            shape = _shape(values[0])
            return ColumnProfile(
                kind="id", detail=f"numeric ID, {unique_share:.0%} unique, shape {shape}", **base
            )
        sign = (
            "all positive"
            if negative == 0
            else "all negative"
            if negative == 1
            else f"{negative:.0%} negative"
        )
        if decimals or money_marks:
            return ColumnProfile(
                kind="amount",
                detail=f"amount with decimals, {sign}",
                negative_share=negative,
                has_decimals=True,
                **base,
            )
        return ColumnProfile(
            kind="integer",
            detail=f"whole numbers {int(min(numbers))}–{int(max(numbers))}, {sign}",
            negative_share=negative,
            **base,
        )

    if distinct <= CATEGORY_LIMIT and unique_share < 0.2:
        return ColumnProfile(
            kind="category",
            detail=f"text with {distinct} distinct values",
            categories=sorted(set(values)) if not personal else [],
            **base,
        )
    if unique_share > 0.9 and all(" " not in v for v in values[:200]):
        shapes = {_shape(v) for v in values[:200]}
        shape = next(iter(shapes)) if len(shapes) == 1 else f"{len(shapes)} shapes"
        return ColumnProfile(
            kind="id", detail=f"ID, {unique_share:.0%} unique, shape {shape}", **base
        )
    return ColumnProfile(kind="text", detail=f"free text, {unique_share:.0%} unique", **base)


def profile_table(table: RawTable) -> list[ColumnProfile]:
    sample = table.data.head(SAMPLE_ROWS)
    return [profile_column(h, sample[h]) for h in table.headers]
