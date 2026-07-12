from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from io import BytesIO, StringIO
from typing import Optional, Union

from .models import Holding
from .tiers import normalize_ticker


SYMBOL_COLUMNS = ("symbol", "ticker", "security symbol")
QUANTITY_COLUMNS = ("quantity", "qty", "shares")
COST_BASIS_COLUMNS = ("cost basis", "costbasis", "total cost basis", "basis")
ACCOUNT_COLUMNS = ("account", "account name")
MARKET_VALUE_COLUMNS = ("value", "market value", "current value")
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


@dataclass(frozen=True)
class ManualHoldingsMapping:
    header_row: int
    symbol_col: int
    quantity_col: int
    price_col: Optional[int] = None
    market_value_col: Optional[int] = None
    description_col: Optional[int] = None


@dataclass(frozen=True)
class ManualHoldingsParseResult:
    holdings: list[Holding]
    warnings: list[str]


@dataclass(frozen=True)
class HoldingsImportSummary:
    total_market_value: Optional[float]
    tickers_with_100_shares: int


def parse_holdings_csv(file: Union[BytesIO, StringIO]) -> list[Holding]:
    raw_text = _read_file_text(file)
    preview = _preview_lines(raw_text)
    try:
        rows = list(csv.reader(StringIO(raw_text)))
    except csv.Error as exc:
        raise _friendly_error(preview, str(exc)) from exc

    mapping = detect_holdings_mapping(rows)
    if mapping is None:
        raise _friendly_error(preview)

    header = [_normalize_column(cell) for cell in rows[mapping.header_row]]
    cost_col = _find_column_index(header, COST_BASIS_COLUMNS)
    account_col = _find_column_index(header, ACCOUNT_COLUMNS)

    holdings: list[Holding] = []
    for row in rows[mapping.header_row + 1 :]:
        normalized_row = [_normalize_column(cell) for cell in row]
        if _is_blank_row(normalized_row):
            continue
        if _looks_like_holdings_header(normalized_row):
            continue
        if _is_account_summary_row(row, mapping.symbol_col, mapping.quantity_col, len(header)):
            continue

        raw_symbol = _cell(row, mapping.symbol_col)
        ticker = normalize_ticker(raw_symbol)
        if not ticker or ticker in {"CASH", "MMDA", "SPAXX"}:
            continue
        if not _is_probable_ticker(ticker):
            continue

        raw_quantity = _cell(row, mapping.quantity_col)
        if not raw_quantity.strip():
            continue
        try:
            shares = int(float(_clean_number(raw_quantity)))
        except (TypeError, ValueError) as exc:
            raise HoldingsCsvError(
                f"Could not parse shares for ticker {ticker!r}. Please check the Quantity/Shares column.",
                preview,
            ) from exc

        cost_basis = None
        if cost_col is not None:
            raw_cost_basis = _cell(row, cost_col)
            try:
                cost_basis = float(_clean_number(raw_cost_basis)) if raw_cost_basis.strip() else None
            except (TypeError, ValueError) as exc:
                raise HoldingsCsvError(
                    f"Could not parse cost basis for ticker {ticker!r}. Please check the Cost Basis column.",
                    preview,
                ) from exc

        account = None
        if account_col is not None:
            raw_account = _cell(row, account_col)
            account = raw_account if raw_account.strip() else None

        holdings.append(Holding(ticker=ticker, shares=shares, cost_basis=cost_basis, account=account))

    if not holdings:
        raise _friendly_error(preview)

    return holdings


def read_holdings_csv_rows(file: Union[BytesIO, StringIO]) -> list[list[str]]:
    raw_text = _read_file_text(file)
    preview = _preview_lines(raw_text)
    try:
        return list(csv.reader(StringIO(raw_text)))
    except csv.Error as exc:
        raise _friendly_error(preview, str(exc)) from exc


def detect_holdings_mapping(rows: list[list[str]]) -> Optional[ManualHoldingsMapping]:
    header_row = _detect_header_row(rows)
    if header_row is None:
        return None

    header = [_normalize_column(cell) for cell in rows[header_row]]
    symbol_col = _find_column_index(header, SYMBOL_COLUMNS)
    quantity_col = _find_column_index(header, QUANTITY_COLUMNS)
    if symbol_col is None or quantity_col is None:
        return None

    return ManualHoldingsMapping(
        header_row=header_row,
        symbol_col=symbol_col,
        quantity_col=quantity_col,
        market_value_col=_find_column_index(header, MARKET_VALUE_COLUMNS),
        description_col=_find_column_index(header, ("description", "security name")),
    )


def normalize_header(value: object) -> str:
    return _normalize_column(value)


def parse_numeric_value(value: object) -> float:
    return float(_clean_number(value))


def parse_holdings_from_mapping(
    rows: list[list[str]],
    mapping: ManualHoldingsMapping,
) -> ManualHoldingsParseResult:
    holdings: list[Holding] = []
    warnings: list[str] = []
    header_len = len(rows[mapping.header_row]) if mapping.header_row < len(rows) else 0

    for row_number, row in enumerate(rows[mapping.header_row + 1 :], start=mapping.header_row + 2):
        normalized_row = [_normalize_column(cell) for cell in row]
        if _is_blank_row(normalized_row):
            continue
        if _looks_like_holdings_header(normalized_row):
            continue
        if _is_account_summary_row(row, mapping.symbol_col, mapping.quantity_col, header_len):
            continue

        raw_symbol = _cell(row, mapping.symbol_col)
        ticker = normalize_ticker(raw_symbol)
        if not ticker or ticker in {"CASH", "MMDA", "SPAXX"}:
            continue
        if not _is_probable_ticker(ticker):
            continue

        raw_quantity = _cell(row, mapping.quantity_col)
        if not raw_quantity.strip():
            continue
        try:
            shares = int(parse_numeric_value(raw_quantity))
        except (TypeError, ValueError):
            warnings.append(f"Skipped row {row_number}: quantity could not be parsed for {ticker}.")
            continue

        holdings.append(Holding(ticker=ticker, shares=shares, cost_basis=None, account=None))

    return ManualHoldingsParseResult(holdings=holdings, warnings=warnings)


def summarize_holdings_import(
    holdings: list[Holding],
    rows: Optional[list[list[str]]] = None,
    mapping: Optional[ManualHoldingsMapping] = None,
) -> HoldingsImportSummary:
    total_market_value = None
    if rows is not None and mapping is not None and mapping.market_value_col is not None:
        total_market_value = _sum_market_values(rows, mapping)
    shares_by_ticker = holdings_to_share_map(holdings)
    return HoldingsImportSummary(
        total_market_value=total_market_value,
        tickers_with_100_shares=sum(1 for shares in shares_by_ticker.values() if shares >= 100),
    )


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


def _detect_header_row(rows: list[list[str]], max_rows: int = 200) -> Optional[int]:
    for index, row in enumerate(rows[:max_rows]):
        if _is_blank_row([_normalize_column(cell) for cell in row]):
            continue
        normalized = [_normalize_column(cell) for cell in row]
        if _looks_like_holdings_header(normalized):
            return index
    return None


def _looks_like_holdings_header(columns: list[str]) -> bool:
    has_symbol = any(column in SYMBOL_COLUMNS for column in columns)
    has_quantity = any(column in QUANTITY_COLUMNS for column in columns)
    return has_symbol and has_quantity


def _normalize_column(value: object) -> str:
    text = str(value).replace("\ufeff", "")
    text = text.strip().strip('"').strip("'").strip()
    return " ".join(text.lower().split())


def _clean_number(value: object) -> str:
    text = str(value).replace("\ufeff", "").strip().strip('"').strip("'").strip()
    is_parenthesized = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = text.replace("$", "").replace(",", "").replace("%", "").strip()
    return f"-{text}" if is_parenthesized and text else text


def _find_column_index(columns: list[str], candidates: tuple[str, ...]) -> Optional[int]:
    for index, column in enumerate(columns):
        if column in candidates:
            return index
    return None


def _is_probable_ticker(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9./-]{0,5}", symbol))


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index]).strip()


def _is_blank_row(row: list[str]) -> bool:
    return all(not cell.strip() for cell in row)


def _is_account_summary_row(row: list[str], symbol_col: int, quantity_col: int, header_len: int) -> bool:
    symbol = _normalize_column(_cell(row, symbol_col))
    if symbol in {"all accounts", "account", "account total", "total", "totals", "individual account"}:
        return True
    if symbol.endswith(" accounts") or symbol.endswith(" account"):
        return True
    raw_quantity = _cell(row, quantity_col)
    if any(marker in raw_quantity for marker in ("$", "%")):
        return True
    if len(row) < header_len and raw_quantity:
        return True
    return False


def _sum_market_values(rows: list[list[str]], mapping: ManualHoldingsMapping) -> Optional[float]:
    if mapping.market_value_col is None:
        return None

    total = 0.0
    found_value = False
    header_len = len(rows[mapping.header_row]) if mapping.header_row < len(rows) else 0
    for row in rows[mapping.header_row + 1 :]:
        normalized_row = [_normalize_column(cell) for cell in row]
        if _is_blank_row(normalized_row):
            continue
        if _looks_like_holdings_header(normalized_row):
            continue
        if _is_account_summary_row(row, mapping.symbol_col, mapping.quantity_col, header_len):
            continue

        ticker = normalize_ticker(_cell(row, mapping.symbol_col))
        if not _is_probable_ticker(ticker):
            continue
        try:
            int(parse_numeric_value(_cell(row, mapping.quantity_col)))
            value = parse_numeric_value(_cell(row, mapping.market_value_col))
        except (TypeError, ValueError):
            continue
        total += value
        found_value = True

    return total if found_value else None


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
