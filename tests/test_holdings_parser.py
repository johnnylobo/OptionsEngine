from __future__ import annotations

from io import BytesIO, StringIO

import pytest

from options_income_engine.holdings import (
    HoldingsCsvError,
    ManualHoldingsMapping,
    detect_holdings_mapping,
    parse_holdings_csv,
    parse_holdings_from_mapping,
    read_holdings_csv_rows,
    summarize_holdings_import,
)
from options_income_engine.models import Holding


def test_parse_clean_simple_holdings_csv() -> None:
    csv = StringIO(
        "Symbol,Quantity,Cost Basis,Account\n"
        "TQQQ,200,8500.00,Merrill\n"
        "NVDA,125,14250.00,Merrill\n"
    )

    assert parse_holdings_csv(csv) == [
        Holding(ticker="TQQQ", shares=200, cost_basis=8500.0, account="Merrill"),
        Holding(ticker="NVDA", shares=125, cost_basis=14250.0, account="Merrill"),
    ]


def test_parse_merrill_style_csv_with_preamble_rows_before_header() -> None:
    csv = StringIO(
        "Merrill Lynch\n"
        "Account Holdings Export\n"
        "Account Number,1234\n"
        "As of,2026-07-09\n"
        "Security Symbol,Security Name,Qty,Last Price,Market Value,Total Cost Basis,Account Name\n"
        "AMD,Advanced Micro Devices,100,155.00,15500.00,15200.00,Brokerage\n"
        "GOOG,Alphabet,80,188.00,15040.00,11200.00,Brokerage\n"
    )

    assert parse_holdings_csv(csv) == [
        Holding(ticker="AMD", shares=100, cost_basis=15200.0, account="Brokerage"),
        Holding(ticker="GOOG", shares=80, cost_basis=11200.0, account="Brokerage"),
    ]


def test_parse_real_merrill_header_with_quoted_symbol_trailing_space() -> None:
    csv = StringIO(
        '"All Accounts","Value","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg"\n'
        '"Brokerage","$125,000.00","$100.00","$12,000.00 10.60%"\n'
        "\n"
        '"Symbol ","Value","Quantity","Price","Day\'s Price $ Chg % Chg","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg","Description"\n'
        '"TQQQ","$16,800.00","200","$84.00","$1.00 1.20%","$200.00","$8,300.00 97.65%","ProShares UltraPro QQQ"\n'
        '"NVDA","$20,500.00","125","$164.00","($2.00) -1.20%","($250.00)","$6,250.00 43.85%","NVIDIA Corp"\n'
        "\n"
        '"All Accounts","Value","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg"\n'
        '"Retirement","$50,000.00","$0.00","$5,000.00 11.10%"\n'
    )

    assert parse_holdings_csv(csv) == [
        Holding(ticker="TQQQ", shares=200, cost_basis=None, account=None),
        Holding(ticker="NVDA", shares=125, cost_basis=None, account=None),
    ]


def test_parse_merrill_header_and_comma_quantity_from_acceptance_sample() -> None:
    csv = StringIO(
        '"All Accounts","Value","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg"\n'
        '"Brokerage","$205,759.84","$0.00","$15,000.00 7.86%"\n'
        "\n"
        '"Symbol  ","Value","Quantity","Price","Day\'s Price $ Chg % Chg","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg","Description"\n'
        '"ABCL","$15,288.00","12,100","$7.28","$0.10 1.39%","$1,210.00","$2,000.00 15.05%","AbCellera Biologics"\n'
        '"AMD","$173,961.84","338","$514.68","$2.00 0.39%","$676.00","$10,000.00 6.10%","Advanced Micro Devices"\n'
    )

    assert parse_holdings_csv(csv) == [
        Holding(ticker="ABCL", shares=12100, cost_basis=None, account=None),
        Holding(ticker="AMD", shares=338, cost_basis=None, account=None),
    ]


def test_real_merrill_sample_auto_parses_holdings_and_summary() -> None:
    csv = StringIO(
        '"All Accounts","Value","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg"\n'
        '"All Accounts","$205,759.84","$0.00","$15,000.00 7.86%"\n'
        '"Individual Account","$100,000.00","$0.00","$5,000.00 5.00%"\n'
        "\n"
        '"Symbol  ","Value","Quantity","Price","Day\'s Price $ Chg % Chg","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg","Description"\n'
        '"ABBV","$16,509.35","65","$253.99","$1.00 0.40%","$65.00","$5,000.00 43.45%","AbbVie Inc"\n'
        '"ABCL","$15,288.00","12,100","$7.28","$0.10 1.39%","$1,210.00","$2,000.00 15.05%","AbCellera Biologics"\n'
        '"AMD","$173,961.84","338","$514.68","$2.00 0.39%","$676.00","$10,000.00 6.10%","Advanced Micro Devices"\n'
        "\n"
        '"Symbol  ","Value","Quantity","Price","Day\'s Price $ Chg % Chg","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg","Description"\n'
        '"IRA","$10,000.00","$0.00","$0.00","","","","Account summary row"\n'
        '"All Accounts","Value","Quantity","Price","Day\'s Price $ Chg % Chg","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg","Description"\n'
    )
    rows = read_holdings_csv_rows(csv)

    holdings = parse_holdings_csv(csv)
    mapping = detect_holdings_mapping(rows)
    summary = summarize_holdings_import(holdings, rows, mapping)

    assert holdings == [
        Holding(ticker="ABBV", shares=65, cost_basis=None, account=None),
        Holding(ticker="ABCL", shares=12100, cost_basis=None, account=None),
        Holding(ticker="AMD", shares=338, cost_basis=None, account=None),
    ]
    assert summary.total_market_value == pytest.approx(205759.19)
    assert summary.tickers_with_100_shares == 2


def test_manual_header_row_mapping_parses_merrill_rows() -> None:
    csv = StringIO(
        '"All Accounts","Value","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg"\n'
        "\n"
        '"Symbol  ","Value","Quantity","Price","Day\'s Price $ Chg % Chg","Day\'s Value Change $","Unrealized Gain/Loss $ Chg % Chg","Description"\n'
        '"ABBV","$16,509.35","65","$253.99","$1.00","$65.00","$5,000.00","AbbVie Inc"\n'
        '"AMD","$173,961.84","338","$514.68","$2.00","$676.00","$10,000.00","Advanced Micro Devices"\n'
    )
    rows = read_holdings_csv_rows(csv)

    result = parse_holdings_from_mapping(
        rows,
        ManualHoldingsMapping(
            header_row=2,
            symbol_col=0,
            quantity_col=2,
            price_col=3,
            market_value_col=1,
            description_col=7,
        ),
    )

    assert result.warnings == []
    assert result.holdings == [
        Holding(ticker="ABBV", shares=65, cost_basis=None, account=None),
        Holding(ticker="AMD", shares=338, cost_basis=None, account=None),
    ]


def test_manual_mapping_parses_quantity_with_comma() -> None:
    csv = StringIO(
        '"Symbol  ","Value","Quantity","Price","Description"\n'
        '"ABCL","$15,288.00","12,100","$7.28","AbCellera Biologics"\n'
    )
    rows = read_holdings_csv_rows(csv)

    result = parse_holdings_from_mapping(rows, ManualHoldingsMapping(header_row=0, symbol_col=0, quantity_col=2))

    assert result.holdings == [
        Holding(ticker="ABCL", shares=12100, cost_basis=None, account=None),
    ]


def test_manual_mapping_skips_malformed_rows_with_warning() -> None:
    csv = StringIO(
        '"Symbol","Quantity","Description"\n'
        '"ABBV","65","AbbVie Inc"\n'
        '"BROKEN","not a number","Bad row"\n'
        '"AMD","338","Advanced Micro Devices"\n'
    )
    rows = read_holdings_csv_rows(csv)

    result = parse_holdings_from_mapping(rows, ManualHoldingsMapping(header_row=0, symbol_col=0, quantity_col=1))

    assert result.holdings == [
        Holding(ticker="ABBV", shares=65, cost_basis=None, account=None),
        Holding(ticker="AMD", shares=338, cost_basis=None, account=None),
    ]
    assert result.warnings == ["Skipped row 3: quantity could not be parsed for BROKEN."]


def test_parse_csv_with_blank_rows_before_header() -> None:
    csv = StringIO(
        "\n\n"
        "Generated by Merrill\n"
        "\n"
        "Symbol,Description,Shares,Price\n"
        "MU,Micron,300,132.00\n"
    )

    assert parse_holdings_csv(csv) == [
        Holding(ticker="MU", shares=300, cost_basis=None, account=None),
    ]


def test_parse_alternate_ticker_and_shares_column_names() -> None:
    csv = BytesIO(
        b"Some disclaimer with no delimiter\n"
        b"Ticker,Security Name,Shares,Current Price\n"
        b"RKLB,Rocket Lab,250,28.00\n"
    )

    assert parse_holdings_csv(csv) == [
        Holding(ticker="RKLB", shares=250, cost_basis=None, account=None),
    ]


def test_malformed_file_raises_friendly_error_with_preview() -> None:
    csv = StringIO(
        "This is not a holdings export\n"
        "Account metadata only\n"
        "No useful table here\n"
    )

    with pytest.raises(HoldingsCsvError) as exc_info:
        parse_holdings_csv(csv)

    message = str(exc_info.value)
    assert "The uploaded file does not look like a holdings CSV." in message
    assert "Please export holdings with Symbol and Quantity/Shares columns." in message
    assert "First 20 lines:" in message
    assert "This is not a holdings export" in message
