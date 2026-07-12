from __future__ import annotations

from datetime import date, timedelta
from io import StringIO

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from options_income_engine.demo import demo_holdings
from options_income_engine.holdings import HoldingsCsvError, parse_holdings_csv
from options_income_engine.models import UserConfig
from options_income_engine.portfolio import build_portfolio_summary
from options_income_engine.preferences import load_ticker_profiles
from options_income_engine.providers import MarketDataError, available_provider_names, build_provider, test_market_data_connection
from options_income_engine.screener import screen_income_candidates
from options_income_engine.tiers import default_watchlist


load_dotenv()

st.set_page_config(page_title="Options Income Engine", page_icon="$", layout="wide")

st.title("Options Income Engine")
st.caption("Local screening only. This app never places trades, logs in to brokers, or stores brokerage credentials.")

with st.sidebar:
    st.header("Inputs")
    provider_options = available_provider_names()
    selected_provider = st.selectbox("Market data provider", provider_options, index=0)
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

demo_mode = selected_provider == "Demo"

with st.expander("Market Data Status", expanded=False):
    st.caption("Use this before screening to verify authentication, options access, bid/ask data, Greeks, timestamps, and data entitlement status.")
    if st.button("Test Market Data Connection"):
        try:
            connection_provider = build_provider(selected_provider)
            result = test_market_data_connection(connection_provider)
            if result.status == "Connected":
                st.success(result.status)
            elif result.status == "Data returned but is delayed":
                st.warning(result.status)
            else:
                st.error(result.status)
            st.write(result.message)
            st.write(
                {
                    "Provider": result.provider,
                    "Quote": result.quote_ok,
                    "Expirations": result.expirations_ok,
                    "Option Chain": result.option_chain_ok,
                    "Bid/Ask": result.has_bid_ask,
                    "Greeks": result.has_greeks,
                    "Timestamps": result.has_timestamps,
                    "Status": result.realtime_status,
                }
            )
        except MarketDataError as exc:
            st.error("Provider unavailable")
            st.write(str(exc))

if uploaded is None and demo_mode:
    holdings = demo_holdings()
    st.success("Loaded demo portfolio with demo market data.")
elif uploaded is None:
    st.info("Upload a holdings CSV to begin, or choose Demo as the market-data provider.")
    st.stop()
else:
    try:
        holdings = parse_holdings_csv(uploaded)
    except HoldingsCsvError as exc:
        st.error(
            "The uploaded file does not look like a holdings CSV.\n\n"
            "Please export holdings with Symbol and Quantity/Shares columns."
        )
        if exc.preview:
            st.code(exc.preview, language="text")
        st.stop()
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
    provider = build_provider(selected_provider)
    profiles = load_ticker_profiles()
    with st.spinner("Fetching option chains and scoring candidates..."):
        portfolio = build_portfolio_summary(
            holdings=holdings,
            provider=provider,
            profiles=profiles,
            cash_balance=available_cash,
        )
        candidates = screen_income_candidates(holdings=holdings, config=config, provider=provider, profiles=profiles)
        provider_health = provider.health()
except MarketDataError as exc:
    st.error(str(exc))
    st.info("Choose Demo mode for a no-key walkthrough, or configure MASSIVE_API_KEY / TRADIER_ACCESS_TOKEN.")
    st.stop()
except Exception as exc:
    st.error(f"Screening failed: {exc}")
    st.stop()

st.subheader("Market Data Status")
status_columns = st.columns(4)
status_columns[0].metric("Data", provider_health.provider)
status_columns[1].metric("Status", provider_health.realtime_status)
last_refresh = provider_health.last_successful_refresh
status_columns[2].metric("Updated", last_refresh.astimezone().strftime("%I:%M:%S %p") if last_refresh else "Not refreshed")
status_columns[3].metric("Health", provider_health.status.title())
if provider_health.message:
    st.warning(provider_health.message)
if provider_health.is_delayed and not provider.is_demo:
    st.warning("Data delayed / stale — recommendations disabled.")
if provider_health.realtime_status == "Unknown":
    st.warning("Data entitlement unknown — the app will not label this feed as real-time.")
if any(candidate.data_is_stale for candidate in candidates):
    st.warning("Some market data is stale. Stale contracts are not labeled as strong candidates.")
if st.button("Refresh Market Data"):
    st.rerun()

if not candidates:
    st.warning("No candidates passed the filters. Stale or missing option bid/ask data disables recommendations.")
    st.stop()

st.subheader("Portfolio Intelligence")
metric_columns = st.columns(4)
metric_columns[0].metric("Portfolio Market Value", f"${portfolio.total_portfolio_market_value:,.2f}")
metric_columns[1].metric("Cash Balance", f"${portfolio.cash_balance:,.2f}")
largest_single = portfolio.largest_single_name
metric_columns[2].metric(
    "Largest Single Name",
    f"{largest_single.ticker} {largest_single.portfolio_weight * 100:.2f}%" if largest_single else "N/A",
)
largest_category = portfolio.largest_category
metric_columns[3].metric(
    "Largest Category",
    f"{largest_category[0]} {largest_category[1] * 100:.2f}%" if largest_category else "N/A",
)

category_df = pd.DataFrame(
    [
        {"Category": category, "Exposure %": weight * 100}
        for category, weight in sorted(portfolio.category_exposure.items())
    ]
)
top_ticker_df = pd.DataFrame(
    [
        {
            "Ticker": holding.ticker,
            "Shares": holding.shares,
            "Current Price": holding.current_price,
            "Market Value": holding.market_value,
            "Category": holding.category,
            "Portfolio Weight %": holding.portfolio_weight * 100,
        }
        for holding in portfolio.top_ticker_exposures
    ]
)
exposure_columns = st.columns(2)
with exposure_columns[0]:
    st.caption("Category Exposure")
    st.dataframe(category_df, use_container_width=True)
with exposure_columns[1]:
    st.caption("Top 10 Ticker Exposures")
    st.dataframe(top_ticker_df, use_container_width=True)

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
    "implied_volatility": "IV",
    "iv_rank": "IV Rank",
    "iv_percentile": "IV Percentile",
    "iv_rank_warning": "IV Rank Note",
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
    "current_ticker_weight": "Current Ticker Weight",
    "current_category_weight": "Current Category Weight",
    "post_assignment_ticker_weight": "Post-Assignment Ticker Weight",
    "post_assignment_category_weight": "Post-Assignment Category Weight",
    "cash_used_if_assigned": "Cash Used If Assigned",
    "shares_remaining_if_called_away": "Shares Remaining If Called Away",
    "portfolio_risk_alerts": "Portfolio Risk Alerts",
    "portfolio_risk_adjustment": "Portfolio Risk Adjustment",
    "data_provider": "Data Provider",
    "data_market_timestamp": "Market Timestamp",
    "data_is_realtime": "Real-Time Data",
    "data_is_delayed": "Delayed Data",
    "data_source_feed": "Source Feed",
    "score": "Score",
    "contracts": "Contracts",
}
df = df[list(display_columns)].rename(columns=display_columns)
for percent_column in [
    "IV Rank",
    "IV",
    "IV Percentile",
    "Est. Assignment Probability",
    "% OTM",
    "Weekly Yield",
    "Annualized Yield",
    "Current Ticker Weight",
    "Current Category Weight",
    "Post-Assignment Ticker Weight",
    "Post-Assignment Category Weight",
]:
    df[percent_column] = df[percent_column] * 100

st.subheader("Ranked Trade Candidates")
st.dataframe(
    df,
    use_container_width=True,
    column_config={
        "Weekly Yield": st.column_config.ProgressColumn("Weekly Yield", format="%.2f%%", min_value=0, max_value=2.0),
        "Annualized Yield": st.column_config.NumberColumn("Annualized Yield", format="%.2f%%"),
        "IV Rank": st.column_config.NumberColumn("IV Rank", format="%.2f%%"),
        "IV": st.column_config.NumberColumn("IV", format="%.2f%%"),
        "IV Percentile": st.column_config.NumberColumn("IV Percentile", format="%.2f%%"),
        "Est. Assignment Probability": st.column_config.NumberColumn("Est. Assignment Probability", format="%.2f%%"),
        "Premium Efficiency Score": st.column_config.NumberColumn("Premium Efficiency Score", format="%.4f"),
        "Preference Adjustment": st.column_config.NumberColumn("Preference Adjustment", format="%.2fx"),
        "% OTM": st.column_config.NumberColumn("% OTM", format="%.2f%%"),
        "Cash Required": st.column_config.NumberColumn("Cash Required", format="$%.2f"),
        "Capital at Risk": st.column_config.NumberColumn("Capital at Risk", format="$%.2f"),
        "Current Ticker Weight": st.column_config.NumberColumn("Current Ticker Weight", format="%.2f%%"),
        "Current Category Weight": st.column_config.NumberColumn("Current Category Weight", format="%.2f%%"),
        "Post-Assignment Ticker Weight": st.column_config.NumberColumn("Post-Assignment Ticker Weight", format="%.2f%%"),
        "Post-Assignment Category Weight": st.column_config.NumberColumn("Post-Assignment Category Weight", format="%.2f%%"),
        "Cash Used If Assigned": st.column_config.NumberColumn("Cash Used If Assigned", format="$%.2f"),
        "Portfolio Risk Adjustment": st.column_config.NumberColumn("Portfolio Risk Adjustment", format="%.2fx"),
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
