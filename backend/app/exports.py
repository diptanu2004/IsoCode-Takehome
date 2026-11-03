"""CSV exports that are safe to open in Excel."""

import csv
import io
from collections.abc import Iterable

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> str:
    """Stop a cell from running as a spreadsheet formula (CSV injection).

    Partner files are outside data; a cell like =HYPERLINK(...) must stay text when finance opens
    the export. Plain negative numbers are left alone.
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_START) and not _is_number(text):
        return "'" + text
    return text


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def to_csv(header: list[str], rows: Iterable[Iterable[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([safe_cell(h) for h in header])
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return buffer.getvalue()
