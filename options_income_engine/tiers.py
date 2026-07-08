TIER_1 = {"TQQQ", "NVDA", "GOOG", "AMZN"}
TIER_2 = {"MU", "AEHR", "NBIS", "IREN", "CRWD", "DELL", "AMD", "SMH", "SOXL", "RKLB", "LUNR"}


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def get_tier(ticker: str) -> str:
    symbol = normalize_ticker(ticker)
    if symbol in TIER_1:
        return "Tier 1 core compounder"
    if symbol in TIER_2:
        return "Tier 2 volatility harvest"
    return "Watchlist/Other"


def default_watchlist() -> list[str]:
    return sorted(TIER_1 | TIER_2)
