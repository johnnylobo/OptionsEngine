from __future__ import annotations

from datetime import date, timedelta
from io import StringIO

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from options_income_engine.holdings import parse_holdings_csv
from options_income_engine.models import UserConfig
from options_income_engine.providers import MarketDataError, build_provider
from options_income_engine.screener import screen_income_candidates
from options_income_engine.tiers import default_watchlist


load_dotenv()

st.set_page_config(page_title="Options Income Engine", page_icon="$", layout="wide")

st.title("Options Income Engine")
st.caption("Local screening only. This app never places trades, logs in to brokers, or stores brokerage credentials.")

with st.sidebar:
    st.header("Inputs")
    uploaded = st.file_uploader("Merrill holdings CSV", type=["csv"])
    watchlist_text = st.text_area("Approved watchlist tickers", value=", ".join(default_watchlist()), height=110)
    available_cash = st.number_input("Available cash", min_value=0.0, value=10000.0, step=500.0, format="%.2f")

    st.subheader("Filters")
    default_expiration = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
    expirations = st.date_input("Target expiration dates", value=[default_expiration])
    delta_range = st.slider("Target delta range", min_value=0.01, max_value=0.30, value=(0.03, 0.10), step=0.01)
    min_weekly_yield_pct = st.number_input("Minimum weekly premium yield (%)", min_value=0.0, value=0.30, step=0.05)
    max_spread_pct = st.number_input("Max bid/ask spread (%)", min_value=1.0, value=25.0, step=1.0)
    run = st.button("Screen candidates", type="primary")

st.warning(
    "For education and screening only. Review assignment risk, earnings dates, spreads, liquidity, and tax consequences before making any trade."
)

if uploaded is None:
    st.info("Upload a holdings CSV to begin. A sample format is included in data/sample_holdings.csv.")
    st.stop()

try:
    holdings = parse_holdings_csv(uploaded)
except Exception as exc:
    st.error(str(exc))
    st.stop()

watchlist = [item.strip().upper() for item in watchlist_text.replace("\n", ",").split(",") if item.strip()]
expiration_values = expirations if isinstance(expirations, list) else [expirations]
config = UserConfig(
    available_cash=available_cash,
    expirations=expiration_values,
    delta_min=float(delta_range[0]),
    delta_max=float(delta_range[1]),
    min_weekly_yield=min_weekly_yield_pct / 100,
    max_spread_pct=max_spread_pct / 100,
    watchlist=watchlist,
)

st.subheader("Portfolio")
st.dataframe(pd.DataFrame([holding.__dict__ for holding in holdings]), use_container_width=True)

if not run:
    st.stop()

try:
    provider = build_provider()
    with st.spinner("Fetching option chains and scoring candidates..."):
        candidates = screen_income_candidates(holdings=holdings, config=config, provider=provider)
except MarketDataError as exc:
    st.error(str(exc))
    st.info("For a no-key demo, set OPTIONS_PROVIDER=mock in your .env file.")
    st.stop()
except Exception as exc:
    st.error(f"Screening failed: {exc}")
    st.stop()

if not candidates:
    st.warning("No candidates passed the filters. Try a wider delta range, lower minimum yield, or a later expiration.")
    st.stop()

df = pd.DataFrame([candidate.__dict__ for candidate in candidates])
display_columns = {
    "rank": "Rank",
    "ticker": "Ticker",
    "strategy": "Strategy",
    "expiration": "Expiration",
    "strike": "Strike",
    "current_price": "Current Price",
    "bid": "Bid",
    "ask": "Ask",
    "mid": "Mid",
    "delta": "Delta",
    "iv_rank": "IV Rank",
    "assignment_probability": "Est. Assignment Probability",
    "premium_efficiency_score": "Premium Efficiency Score",
    "premium_per_contract": "Premium / Contract",
    "total_premium": "Total Premium",
    "shares_covered": "Shares Covered",
    "cash_required": "Cash Required",
    "capital_at_risk": "Capital at Risk",
    "assignment_outcome": "Assignment Outcome",
    "effective_entry_price": "Effective Entry Price",
    "percent_otm": "% OTM",
    "weekly_yield": "Weekly Yield",
    "annualized_yield": "Annualized Yield",
    "liquidity_warning": "Liquidity Warning",
    "earnings_warning": "Earnings Warning",
    "recommendation": "Recommendation",
    "suggested_limit_price": "Suggested Limit Price",
    "tier": "Tier",
    "category": "Category",
    "own_more_score": "Own More Score",
    "happy_to_sell_score": "Happy To Sell Score",
    "max_contracts": "Max Contracts",
    "profile_notes": "Profile Notes",
    "preference_adjustment": "Preference Adjustment",
    "score": "Score",
    "contracts": "Contracts",
}
df = df[list(display_columns)].rename(columns=display_columns)
for percent_column in ["IV Rank", "Est. Assignment Probability", "% OTM", "Weekly Yield", "Annualized Yield"]:
    df[percent_column] = df[percent_column] * 100

st.subheader("Ranked Trade Candidates")
st.dataframe(
    df,
    use_container_width=True,
    column_config={
        "Weekly Yield": st.column_config.ProgressColumn("Weekly Yield", format="%.2f%%", min_value=0, max_value=2.0),
        "Annualized Yield": st.column_config.NumberColumn("Annualized Yield", format="%.2f%%"),
        "IV Rank": st.column_config.NumberColumn("IV Rank", format="%.2f%%"),
        "Est. Assignment Probability": st.column_config.NumberColumn("Est. Assignment Probability", format="%.2f%%"),
        "Premium Efficiency Score": st.column_config.NumberColumn("Premium Efficiency Score", format="%.4f"),
        "Preference Adjustment": st.column_config.NumberColumn("Preference Adjustment", format="%.2fx"),
        "% OTM": st.column_config.NumberColumn("% OTM", format="%.2f%%"),
        "Cash Required": st.column_config.NumberColumn("Cash Required", format="$%.2f"),
        "Capital at Risk": st.column_config.NumberColumn("Capital at Risk", format="$%.2f"),
    },
)

csv = df.to_csv(index=False)
st.download_button(
    "Export results to CSV",
    data=csv,
    file_name="options_income_candidates.csv",
    mime="text/csv",
)

with st.expander("Safety notes"):
    st.write(
        "Rejected earnings trades are excluded from the ranked table. Wide spreads, low volume, and low open interest are flagged. Suggested limit prices use at least the midpoint and are not orders."
    )
