"""Read a partner's CSV or Excel file into a table of text cells, whatever sits above the header."""

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

import fastexcel
import polars as pl

HEAD_ROWS = 30  # the header must be within the first 30 rows
_NUMBER = re.compile(r"^[\s₹(+-]*[\d,]+(\.\d+)?\)?\s*$")


class FileReadError(ValueError):
    """The file can't be read as a table. The message is shown to the user."""


@dataclass
class RawTable:
    headers: list[str]
    data: pl.DataFrame  # every column is text; column names are the headers
    header_row: int  # 1-based row of the header in the file
    first_row_no: int  # file row number of data.row(0)


def find_header_row(rows: list[list[str]]) -> int:
    """Index of the first row that looks like column names: wide, and no cell is a number."""
    widths = [sum(1 for c in row if str(c or "").strip()) for row in rows]
    widest = max(widths, default=0)
    for i, (row, width) in enumerate(zip(rows, widths, strict=True)):
        cells = [str(c).strip() for c in row if str(c or "").strip()]
        if width >= 2 and width >= 0.6 * widest and not any(_NUMBER.match(c) for c in cells):
            return i
    raise FileReadError("Couldn't find a row of column names in the first 30 rows.")


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FileReadError("The file isn't readable text. Save it as UTF-8 CSV or as .xlsx.")


def _unique_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for i, header in enumerate(headers):
        name = str(header or "").strip() or f"Column {i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        result.append(name)
    return result


def read_csv(raw: bytes) -> RawTable:
    text = _decode(raw)
    lines = text.splitlines(keepends=True)
    head = [next(csv.reader([line]), []) for line in lines[:HEAD_ROWS]]
    header_index = find_header_row(head)
    headers = _unique_headers(head[header_index])

    body = "".join(lines[header_index + 1 :])
    if not body.strip():
        data = pl.DataFrame({h: [] for h in headers}, schema=dict.fromkeys(headers, pl.String))
    else:
        try:
            data = pl.read_csv(
                io.StringIO(body),
                has_header=False,
                infer_schema=False,
                schema=dict.fromkeys(headers, pl.String),
                missing_columns="insert",
                extra_columns="ignore",
            )
        except pl.exceptions.PolarsError as exc:
            raise FileReadError(f"The CSV couldn't be parsed: {exc}") from exc
    return RawTable(headers, data, header_index + 1, header_index + 2)


def read_xlsx(raw: bytes) -> RawTable:
    try:
        book = fastexcel.read_excel(raw)
        head = book.load_sheet(0, header_row=None, n_rows=HEAD_ROWS, dtypes="string")
    except Exception as exc:  # fastexcel raises its own error types per failure
        raise FileReadError("The Excel file couldn't be opened. Is it a valid .xlsx?") from exc
    head_rows = [[c or "" for c in row] for row in head.to_polars().rows()]
    header_index = find_header_row(head_rows)
    headers = _unique_headers(head_rows[header_index])
    sheet = book.load_sheet(
        0, header_row=None, skip_rows=header_index + 1, dtypes="string", column_names=headers
    )
    data = sheet.to_polars()
    return RawTable(headers, data, header_index + 1, header_index + 2)


def read_table(path: Path) -> RawTable:
    raw = path.read_bytes()
    if path.suffix.lower() == ".xlsx":
        return read_xlsx(raw)
    return read_csv(raw)
