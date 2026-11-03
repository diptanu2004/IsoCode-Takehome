from datetime import date

import polars as pl
import pytest

from app.ingest import normalise
from app.ingest.reader import FileReadError, find_header_row, read_csv


def _run(expr: pl.Expr, values: list) -> list:
    return (
        pl.DataFrame({"v": values}, schema={"v": pl.String})
        .select(expr.alias("out"))["out"]
        .to_list()
    )


@pytest.mark.parametrize(
    ("text", "paise"),
    [
        ("1199.00", 119900),
        ("1,199.00", 119900),
        ("₹1,19,900.50", 11990050),
        ("Rs. 862", 86200),
        ("INR 30", 3000),
        ("(180.00)", -18000),
        ("(₹7.14)", -714),
        ("-161.82", -16182),
        ("1165.36", 116536),  # float noise must not lose a paisa
        ("  42 ", 4200),
        ("", None),
        ("n/a", None),
    ],
)
def test_money(text, paise):
    assert _run(normalise.money(pl.col("v")), [text]) == [paise]


def test_money_negate_flips_deductions_shown_as_positive():
    assert _run(normalise.money(pl.col("v"), negate=True), ["180.00", "(5.00)"]) == [-18000, 500]


@pytest.mark.parametrize(
    ("text", "clean"),
    [
        ("ord-m-88120", "ORD-M-88120"),
        (" ORD-W-204118 ", "ORD-W-204118"),
        ("4013631710.0", "4013631710"),  # Excel number cell read as text
        ("4.01363171E+09", "4013631710"),  # Excel scientific notation
        ("790000204377", "790000204377"),
        ("", None),
    ],
)
def test_identifier(text, clean):
    assert _run(normalise.identifier(pl.col("v")), [text]) == [clean]


def test_identifier_strips_line_suffix():
    out = _run(normalise.identifier(pl.col("v"), strip_suffix=r"-\d+$"), ["m3-ab12cd34-1"])
    assert out == ["M3-AB12CD34"]


@pytest.mark.parametrize(
    "text",
    [
        "2026-11-03",
        "03/11/2026",
        "03-11-2026",
        "03-Nov-2026",
        "03 Nov 2026",
        "2026-11-03 00:00:00",
        "46329",
    ],
)
def test_date_formats_without_a_saved_format(text):
    assert _run(normalise.date(pl.col("v")), [text]) == [date(2026, 11, 3)]


def test_date_uses_the_saved_format_when_given():
    # 03/11 is 3 November in dd/mm, and must not be read as 11 March.
    assert _run(normalise.date(pl.col("v"), "%d/%m/%Y"), ["03/11/2026"]) == [date(2026, 11, 3)]
    assert _run(normalise.date(pl.col("v"), "%d/%m/%Y"), ["2026-11-03"]) == [None]


def test_datetime_is_local_time():
    [value] = _run(normalise.datetime(pl.col("v")), ["2026-10-19 09:44"])
    assert value.hour == 9 and str(value.tzinfo) == "Asia/Kolkata"


def test_weight_in_kg_becomes_grams():
    assert _run(normalise.integer(pl.col("v"), scale=1000), ["0.724", "1.2"]) == [724, 1200]


def test_choice_maps_partner_labels_case_insensitively():
    expr = normalise.choice(
        pl.col("v"), {"RTO": "return", "Forward": "forward"}, ["forward", "return"]
    )
    assert _run(expr, ["rto", "FORWARD", "return", "sideways"]) == [
        "return",
        "forward",
        "return",
        None,
    ]


def test_header_row_is_found_below_title_rows():
    rows = [
        ["Marketplace 2 Seller Payout Report"],
        ["Period: 1 Sep to 3 Nov"],
        [],
        ["Order Item ID", "Payout Date", "Item Price"],
        ["4013631710", "2026-09-15", "899"],
    ]
    assert find_header_row(rows) == 3


def test_a_row_of_numbers_is_never_taken_as_the_header():
    with pytest.raises(FileReadError):
        find_header_row([["1", "2", "3"], ["4", "5", "6"]])


def test_csv_reader_handles_bom_title_rows_and_ragged_lines():
    raw = "﻿Report\n\nOrder ID,Amount,Note\nA1,10\nA2,20,x,extra\n".encode()
    table = read_csv(raw)
    assert table.headers == ["Order ID", "Amount", "Note"]
    assert table.header_row == 3 and table.first_row_no == 4
    assert table.data.rows() == [("A1", "10", None), ("A2", "20", "x")]


def test_csv_reader_falls_back_to_windows_encoding():
    table = read_csv("Order ID,Customer\nA1,Zoë\n".encode("cp1252"))
    assert table.data.rows() == [("A1", "Zoë")]


def test_duplicate_headers_are_kept_apart():
    assert read_csv(b"Fee,Fee,\n1,2,3\n").headers == ["Fee", "Fee (2)", "Column 3"]
