from __future__ import annotations

from .models import Holding


def demo_holdings() -> list[Holding]:
    return [
        Holding(ticker="TQQQ", shares=200, cost_basis=8500.0, account="Demo"),
        Holding(ticker="NVDA", shares=125, cost_basis=14250.0, account="Demo"),
        Holding(ticker="GOOG", shares=100, cost_basis=11200.0, account="Demo"),
        Holding(ticker="AMZN", shares=100, cost_basis=18500.0, account="Demo"),
        Holding(ticker="MU", shares=300, cost_basis=28500.0, account="Demo"),
        Holding(ticker="AEHR", shares=400, cost_basis=6200.0, account="Demo"),
        Holding(ticker="CRWD", shares=100, cost_basis=32000.0, account="Demo"),
        Holding(ticker="IREN", shares=500, cost_basis=4500.0, account="Demo"),
        Holding(ticker="AMD", shares=100, cost_basis=15200.0, account="Demo"),
        Holding(ticker="RKLB", shares=300, cost_basis=7200.0, account="Demo"),
    ]
