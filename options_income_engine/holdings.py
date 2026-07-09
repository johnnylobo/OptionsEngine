from __future__ import annotations

import csv
from io import BytesIO, StringIO
from typing import Optional, Union

import pandas as pd

from .models import Holding
from .tiers import normalize_ticker


SYMBOL_COLUMNS = ("symbol", "ticker", "security symbol")
QUANTITY_COLUMNS = ("quantity", "qty", "shares")
COST_BASIS_COLUMNS = ("cost basis", "costbasis", "total cost basis", "basis")
ACCOUNT_COLUMNS = ("account", "account name")
HEADER_HINT_COLUMNS = (
    "symbol",
    "ticker",
    "security symbol",
    "quantity",
    "shares",
    "qty",
    "last price",
    "price",
    "current price",
    "market value",
    "description",
    "security name",
)
DEBUG_LINE_COUNT = 20


class HoldingsCsvError(ValueError):
    def __init__(self, message: str, preview: str = "") -> None:
        self.preview = preview
        details = (
            f"{message}\n\nFirst {DEBUG_LINE_COUNT} lines:\n{preview}"
            if preview
            else message
        )
        super().__init__(details)


def _find_column(columns: list[str], candidates: tuple[str, ...]) -> Optional[str]:
    lookup = {column.lower().strip(): column for column in columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    return None


def parse_holdings_csv(file: Union[BytesIO, StringIO]) -> list[Holding]:
    raw_text = _read_file_text(file)
    preview = _preview_lines(raw_text)
    header_row = _detect_header_row(raw_text)
    if header_row is None:
        raise _friendly_error(preview)

    try:
        df = pd.read_csv(
            StringIO(raw_text),
            skiprows=header_row,
            engine="python",
            skip_blank_lines=True,
            on_bad_lines="error",
        )
    except Exception as exc:
        raise _friendly_error(preview, str(exc)) from exc

    if df.empty:
        return []

    columns = list(df.columns)
    symbol_col = _find_column(columns, SYMBOL_COLUMNS)
    quantity_col = _find_column(columns, QUANTITY_COLUMNS)
    cost_col = _find_column(columns, COST_BASIS_COLUMNS)
    account_col = _find_column(columns, ACCOUNT_COLUMNS)

    if symbol_col is None or quantity_col is None:
        raise _friendly_error(preview)

    holdings: list[Holding] = []
    for _, row in df.iterrows():
        raw_symbol = row.get(symbol_col)
        if pd.isna(raw_symbol):
            continue
        ticker = normalize_ticker(str(raw_symbol))
        if not ticker or ticker in {"CASH", "MMDA", "SPAXX"}:
            continue

        try:
            shares = int(float(_clean_number(row.get(quantity_col, 0))))
        except (TypeError, ValueError) as exc:
            raise HoldingsCsvError(
                f"Could not parse shares for ticker {ticker!r}. Please check the Quantity/Shares column.",
                preview,
            ) from exc
        cost_basis = None
        if cost_col is not None and not pd.isna(row.get(cost_col)):
            try:
                cost_basis = float(_clean_number(row.get(cost_col)))
            except (TypeError, ValueError) as exc:
                raise HoldingsCsvError(
                    f"Could not parse cost basis for ticker {ticker!r}. Please check the Cost Basis column.",
                    preview,
                ) from exc
        account = None
        if account_col is not None and not pd.isna(row.get(account_col)):
            account = str(row.get(account_col))

        holdings.append(Holding(ticker=ticker, shares=shares, cost_basis=cost_basis, account=account))

    return holdings


def _read_file_text(file: Union[BytesIO, StringIO]) -> str:
    if hasattr(file, "seek"):
        file.seek(0)
    raw = file.read()
    if isinstance(raw, bytes):
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _detect_header_row(raw_text: str, max_rows: int = 50) -> Optional[int]:
    sample = raw_text.splitlines()[:max_rows]
    for index, line in enumerate(sample):
        if not line.strip():
            continue
        try:
            row = next(csv.reader([line]))
        except csv.Error:
            continue
        normalized = [_normalize_column(cell) for cell in row]
        if _looks_like_holdings_header(normalized):
            return index
    return None


def _looks_like_holdings_header(columns: list[str]) -> bool:
    has_symbol = any(column in SYMBOL_COLUMNS for column in columns)
    has_quantity = any(column in QUANTITY_COLUMNS for column in columns)
    hint_count = sum(1 for column in columns if column in HEADER_HINT_COLUMNS)
    return has_symbol and has_quantity and hint_count >= 2


def _normalize_column(value: object) -> str:
    return str(value).strip().lower()


def _clean_number(value: object) -> str:
    return str(value).replace("$", "").replace(",", "").strip()


def _preview_lines(raw_text: str, line_count: int = DEBUG_LINE_COUNT) -> str:
    return "\n".join(raw_text.splitlines()[:line_count])


def _friendly_error(preview: str, parser_error: str = "") -> HoldingsCsvError:
    message = (
        "The uploaded file does not look like a holdings CSV.\n"
        "Please export holdings with Symbol and Quantity/Shares columns."
    )
    if parser_error:
        message = f"{message}\nParser detail: {parser_error}"
    return HoldingsCsvError(message, preview)


def holdings_to_share_map(holdings: list[Holding]) -> dict[str, int]:
    shares_by_ticker: dict[str, int] = {}
    for holding in holdings:
        shares_by_ticker[holding.ticker] = shares_by_ticker.get(holding.ticker, 0) + holding.shares
    return shares_by_ticker
