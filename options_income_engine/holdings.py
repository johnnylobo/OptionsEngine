from __future__ import annotations

from io import BytesIO, StringIO
from typing import Optional, Union

import pandas as pd

from .models import Holding
from .tiers import normalize_ticker


SYMBOL_COLUMNS = ("symbol", "ticker", "security symbol")
QUANTITY_COLUMNS = ("quantity", "qty", "shares")
COST_BASIS_COLUMNS = ("cost basis", "costbasis", "total cost basis", "basis")
ACCOUNT_COLUMNS = ("account", "account name")


def _find_column(columns: list[str], candidates: tuple[str, ...]) -> Optional[str]:
    lookup = {column.lower().strip(): column for column in columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    return None


def parse_holdings_csv(file: Union[BytesIO, StringIO]) -> list[Holding]:
    df = pd.read_csv(file)
    if df.empty:
        return []

    columns = list(df.columns)
    symbol_col = _find_column(columns, SYMBOL_COLUMNS)
    quantity_col = _find_column(columns, QUANTITY_COLUMNS)
    cost_col = _find_column(columns, COST_BASIS_COLUMNS)
    account_col = _find_column(columns, ACCOUNT_COLUMNS)

    if symbol_col is None or quantity_col is None:
        raise ValueError(
            "Holdings CSV needs a symbol/ticker column and a quantity/shares column."
        )

    holdings: list[Holding] = []
    for _, row in df.iterrows():
        raw_symbol = row.get(symbol_col)
        if pd.isna(raw_symbol):
            continue
        ticker = normalize_ticker(str(raw_symbol))
        if not ticker or ticker in {"CASH", "MMDA", "SPAXX"}:
            continue

        shares = int(float(str(row.get(quantity_col, 0)).replace(",", "")))
        cost_basis = None
        if cost_col is not None and not pd.isna(row.get(cost_col)):
            cost_basis = float(str(row.get(cost_col)).replace("$", "").replace(",", ""))
        account = None
        if account_col is not None and not pd.isna(row.get(account_col)):
            account = str(row.get(account_col))

        holdings.append(Holding(ticker=ticker, shares=shares, cost_basis=cost_basis, account=account))

    return holdings


def holdings_to_share_map(holdings: list[Holding]) -> dict[str, int]:
    shares_by_ticker: dict[str, int] = {}
    for holding in holdings:
        shares_by_ticker[holding.ticker] = shares_by_ticker.get(holding.ticker, 0) + holding.shares
    return shares_by_ticker
