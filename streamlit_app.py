from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from options_income_engine.dashboard import (
    aggregate_risk_alerts,
    build_dashboard_summary,
    calculate_income_forecasts,
    explain_candidate,
    filter_candidates,
    select_best_opportunities,
)
from options_income_engine.demo import demo_holdings
from options_income_engine.holdings import (
    HoldingsCsvError,
    ManualHoldingsMapping,
    detect_holdings_mapping,
    parse_holdings_csv,
    parse_holdings_from_mapping,
    read_holdings_csv_rows,
    summarize_holdings_import,
)
from options_income_engine.models import UserConfig
from options_income_engine.portfolio import build_portfolio_summary
from options_income_engine.preferences import load_ticker_profiles
from options_income_engine.providers import MarketDataError, available_provider_names, build_provider, test_market_data_connection
from options_income_engine.screener import screen_income_candidates
from options_income_engine.tiers import default_watchlist


MERRILL_MAPPING_PATH = Path(__file__).with_name(".merrill_default_mapping.json")


def _load_saved_merrill_mapping() -> Optional[ManualHoldingsMapping]:
    if not MERRILL_MAPPING_PATH.exists():
        return None
    try:
        data = json.loads(MERRILL_MAPPING_PATH.read_text())
        return ManualHoldingsMapping(
            header_row=int(data["header_row"]),
            symbol_col=int(data["symbol_col"]),
            quantity_col=int(data["quantity_col"]),
            price_col=data.get("price_col"),
            market_value_col=data.get("market_value_col"),
            description_col=data.get("description_col"),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _save_merrill_mapping(mapping: ManualHoldingsMapping) -> None:
    MERRILL_MAPPING_PATH.write_text(
        json.dumps(
            {
                "header_row": mapping.header_row,
                "symbol_col": mapping.symbol_col,
                "quantity_col": mapping.quantity_col,
                "price_col": mapping.price_col,
                "market_value_col": mapping.market_value_col,
                "description_col": mapping.description_col,
            },
            indent=2,
        )
    )


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

demo_mode = False
raw_rows = None
mapping_used = None
holdings = None

if uploaded is None:
    st.session_state["use_demo_portfolio"] = st.session_state.get("use_demo_portfolio", False)
    st.info("Upload a Merrill holdings CSV, or use the demo portfolio to explore the dashboard.")
    if st.button("Use Demo Portfolio", type="primary"):
        st.session_state["use_demo_portfolio"] = True

    if st.session_state["use_demo_portfolio"]:
        holdings = demo_holdings()
        demo_mode = True
        st.success("Loaded demo portfolio with demo market data.")
    else:
        st.stop()
else:
    st.session_state["use_demo_portfolio"] = False
    upload_signature = (getattr(uploaded, "name", "uploaded.csv"), getattr(uploaded, "size", None))
    try:
        raw_rows = read_holdings_csv_rows(uploaded)
    except HoldingsCsvError:
        raw_rows = None

    try:
        holdings = parse_holdings_csv(uploaded)
        mapping_used = detect_holdings_mapping(raw_rows) if raw_rows is not None else None
        st.session_state.pop("manual_holdings_signature", None)
        st.session_state.pop("manual_holdings", None)
    except HoldingsCsvError:
        saved_mapping = _load_saved_merrill_mapping()
        if raw_rows is not None and saved_mapping is not None:
            result = parse_holdings_from_mapping(raw_rows, saved_mapping)
            if result.holdings:
                holdings = result.holdings
                mapping_used = saved_mapping
                st.success(f"Imported {len(holdings)} holdings using your saved Merrill mapping.")
                if result.warnings:
                    with st.expander("Rows skipped during import"):
                        for warning in result.warnings:
                            st.warning(warning)

        if holdings is None and (
            st.session_state.get("manual_holdings_signature") == upload_signature
            and st.session_state.get("manual_holdings")
        ):
            holdings = st.session_state["manual_holdings"]
            mapping_used = st.session_state.get("manual_holdings_mapping")
            st.success(f"Using saved import mapping for {len(holdings)} holdings.")

        if holdings is None:
            st.warning("Automatic import could not identify holdings. Use this wizard once and save the mapping.")
            try:
                raw_rows = raw_rows if raw_rows is not None else read_holdings_csv_rows(uploaded)
            except HoldingsCsvError:
                st.error("I could not read this CSV file. Please export holdings as CSV from Merrill, then upload that file here.")
                st.stop()

            preview_rows = raw_rows[:100]
            max_columns = max((len(row) for row in preview_rows), default=0)
            preview_df = pd.DataFrame(
                [row + [""] * (max_columns - len(row)) for row in preview_rows],
                index=range(1, len(preview_rows) + 1),
            )
            st.subheader("Holdings Import Wizard")
            st.caption("Previewing the first 100 rows. Choose the row with Symbol and Quantity column names.")
            st.dataframe(preview_df, use_container_width=True)

            header_row_number = st.number_input(
                "Header row number",
                min_value=1,
                max_value=max(len(raw_rows), 1),
                value=1,
                step=1,
                help="Use the row number shown at the left of the preview table.",
            )
            header_index = int(header_row_number) - 1
            selected_header = raw_rows[header_index] if header_index < len(raw_rows) else []
            column_options = [
                (f"{index + 1}: {name.strip() or '(blank)'}", index)
                for index, name in enumerate(selected_header)
            ]
            option_labels = [label for label, _ in column_options]
            if not option_labels:
                st.info("Pick a header row that contains column names, then map Symbol and Quantity.")
                st.stop()

            option_lookup = {label: index for label, index in column_options}
            optional_labels = ["None"] + option_labels

            symbol_label = st.selectbox("Symbol column", option_labels)
            quantity_label = st.selectbox("Quantity/Shares column", option_labels)
            price_label = st.selectbox("Optional Price column", optional_labels)
            market_value_label = st.selectbox("Optional Market Value column", optional_labels)
            description_label = st.selectbox("Optional Description column", optional_labels)
            save_default_mapping = st.checkbox("Save this as Merrill default mapping", value=True)

            if st.button("Use this mapping", type="primary"):
                mapping = ManualHoldingsMapping(
                    header_row=header_index,
                    symbol_col=option_lookup[symbol_label],
                    quantity_col=option_lookup[quantity_label],
                    price_col=None if price_label == "None" else option_lookup[price_label],
                    market_value_col=None if market_value_label == "None" else option_lookup[market_value_label],
                    description_col=None if description_label == "None" else option_lookup[description_label],
                )
                result = parse_holdings_from_mapping(raw_rows, mapping)
                if not result.holdings:
                    st.error("No holdings could be imported. Pick the row with Symbol and Quantity, then try again.")
                    if result.warnings:
                        st.warning("\n".join(result.warnings[:10]))
                    st.stop()
                holdings = result.holdings
                st.session_state["manual_holdings_signature"] = upload_signature
                st.session_state["manual_holdings"] = holdings
                st.session_state["manual_holdings_mapping"] = mapping
                mapping_used = mapping
                if save_default_mapping:
                    try:
                        _save_merrill_mapping(mapping)
                        st.success("Saved this as your Merrill default mapping.")
                    except OSError:
                        st.warning("Imported holdings, but could not save the default mapping.")
                st.success(f"Imported {len(holdings)} holdings with manual mapping.")
                if result.warnings:
                    with st.expander("Rows skipped during import"):
                        for warning in result.warnings:
                            st.warning(warning)
            else:
                st.stop()
    except Exception:
        st.error("Something went wrong while reading the holdings file. Try the import wizard or use the demo portfolio.")
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

import_summary = summarize_holdings_import(holdings, raw_rows, mapping_used)
st.subheader("Imported Holdings")
st.success(f"Imported {len(holdings)} holdings")
summary_columns = st.columns(3)
if import_summary.total_market_value is None:
    summary_columns[0].metric("Total Portfolio Value", "Not available")
else:
    summary_columns[0].metric("Total Portfolio Value", f"${import_summary.total_market_value:,.2f}")
summary_columns[1].metric("Tickers With 100+ Shares", import_summary.tickers_with_100_shares)
summary_columns[2].metric("Imported Tickers", len({holding.ticker for holding in holdings}))
st.dataframe(pd.DataFrame([holding.__dict__ for holding in holdings]), use_container_width=True)

if not run:
    st.stop()

try:
    provider = build_provider("Demo" if demo_mode else selected_provider)
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
    st.info("Choose Demo for a no-key walkthrough, or configure MASSIVE_API_KEY / TRADIER_ACCESS_TOKEN.")
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
    st.warning("Data delayed / stale - recommendations disabled.")
if provider_health.realtime_status == "Unknown":
    st.warning("Data entitlement unknown - the app will not label this feed as real-time.")
if any(candidate.data_is_stale for candidate in candidates):
    st.warning("Some market data is stale. Stale contracts are not labeled as strong candidates.")
if st.button("Refresh Market Data"):
    st.rerun()

if not candidates:
    st.warning("No candidates passed the filters. Stale or missing option bid/ask data disables recommendations.")
    st.stop()

summary = build_dashboard_summary(candidates, portfolio)
best_opportunities = select_best_opportunities(candidates)
income_forecasts = calculate_income_forecasts(candidates)
risk_alerts = aggregate_risk_alerts(candidates, portfolio)

st.subheader("Executive Dashboard")
summary_columns = st.columns(4)
summary_columns[0].metric("Total Portfolio Value", f"${summary.total_portfolio_value:,.2f}")
summary_columns[1].metric("Cash Available", f"${summary.cash_available:,.2f}")
summary_columns[2].metric("Candidates", f"{summary.candidate_count}")
summary_columns[3].metric("Sell Recommendations", f"{summary.sell_recommendation_count}")

summary_columns = st.columns(4)
summary_columns[0].metric("Best Covered Call Efficiency", f"{summary.best_covered_call_efficiency:.4f}")
summary_columns[1].metric("Best Put Efficiency", f"{summary.best_cash_secured_put_efficiency:.4f}")
summary_columns[2].metric("Sell-Rated Premium", f"${summary.sell_rated_premium_available:,.2f}")
summary_columns[3].metric("Sell Put Cash Required", f"${summary.sell_rated_put_cash_required:,.2f}")

st.subheader("Best Opportunities")
opportunity_columns = st.columns(3)
for column, title, candidate in [
    (opportunity_columns[0], "Best Covered Call", best_opportunities.best_covered_call),
    (opportunity_columns[1], "Best Cash-Secured Put", best_opportunities.best_cash_secured_put),
    (opportunity_columns[2], "Best Overall Trade", best_opportunities.best_overall_trade),
]:
    with column:
        st.markdown(f"**{title}**")
        if candidate is None:
            st.info("No candidate available.")
        else:
            st.metric(candidate.ticker, f"${candidate.total_premium:,.2f}", candidate.strategy)
            st.write(
                {
                    "Expiration": candidate.expiration,
                    "Strike": f"${candidate.strike:.2f}",
                    "Premium Efficiency": f"{candidate.premium_efficiency_score:.4f}",
                    "Assignment Probability": f"{candidate.assignment_probability * 100:.2f}%",
                    "Assignment Outcome": candidate.assignment_outcome,
                    "Portfolio Risk Alerts": candidate.portfolio_risk_alerts or "None",
                    "Why": explain_candidate(candidate),
                }
            )

st.subheader("Income Forecast")
forecast_df = pd.DataFrame(
    [
        {
            "Scenario": forecast.label,
            "Total Premium": forecast.total_premium,
            "Cash Required for Puts": forecast.cash_required_for_puts,
            "Shares Covered for Calls": forecast.shares_covered_for_calls,
            "Average Assignment Probability": forecast.average_assignment_probability * 100,
        }
        for forecast in income_forecasts
    ]
)
st.caption("Available opportunity premium from the current screen only.")
st.dataframe(
    forecast_df,
    use_container_width=True,
    column_config={
        "Total Premium": st.column_config.NumberColumn("Total Premium", format="$%.2f"),
        "Cash Required for Puts": st.column_config.NumberColumn("Cash Required for Puts", format="$%.2f"),
        "Average Assignment Probability": st.column_config.NumberColumn(
            "Average Assignment Probability",
            format="%.2f%%",
        ),
    },
)

st.subheader("Portfolio Health")
health_columns = st.columns(4)
health_columns[0].metric("Portfolio Market Value", f"${portfolio.total_portfolio_market_value:,.2f}")
health_columns[1].metric("Cash Balance", f"${portfolio.cash_balance:,.2f}")
health_columns[2].metric(
    "Cash %",
    f"{(portfolio.cash_balance / portfolio.total_account_value * 100) if portfolio.total_account_value else 0:.2f}%",
)
largest_single = portfolio.largest_single_name
health_columns[3].metric(
    "Largest Single Name",
    f"{largest_single.ticker} {largest_single.portfolio_weight * 100:.2f}%" if largest_single else "N/A",
)
largest_category = portfolio.largest_category

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
    st.caption(
        f"Largest Category: {largest_category[0]} {largest_category[1] * 100:.2f}%"
        if largest_category
        else "Category Exposure"
    )
    st.dataframe(category_df, use_container_width=True)
with exposure_columns[1]:
    st.caption("Top 10 Ticker Exposures")
    st.dataframe(top_ticker_df, use_container_width=True)

st.subheader("Risk Alerts")
if risk_alerts:
    for alert in risk_alerts:
        st.warning(alert)
else:
    st.success("No major risk alerts in the current screen.")

st.subheader("Ranked Trade Candidates")
filter_columns = st.columns(4)
strategy_filter = filter_columns[0].selectbox(
    "Strategy",
    ["All", "Covered Calls", "Cash-Secured Puts"],
)
recommendation_filter = filter_columns[1].selectbox(
    "Recommendation",
    ["All", "Sell", "Maybe", "Skip"],
)
ticker_search = filter_columns[2].text_input("Ticker search")
min_efficiency = filter_columns[3].number_input(
    "Minimum Premium Efficiency Score",
    min_value=0.0,
    value=0.0,
    step=0.01,
)
filtered_candidates = filter_candidates(
    candidates,
    strategy=strategy_filter,
    recommendation=recommendation_filter,
    ticker_search=ticker_search,
    min_premium_efficiency_score=float(min_efficiency),
)

if not filtered_candidates:
    st.info("No candidates match the selected filters.")
    st.stop()

df = pd.DataFrame([candidate.__dict__ for candidate in filtered_candidates])
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
    df[percent_column] = pd.to_numeric(df[percent_column], errors="coerce") * 100

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
