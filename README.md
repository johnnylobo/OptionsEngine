# Options Income Engine

A local Streamlit app that screens a manually uploaded Merrill holdings CSV and an approved watchlist for both sides of an options income strategy:

- Covered calls on shares already owned.
- Cash-secured puts on names already owned or additionally approved.

It ranks candidate trades only; it never submits orders or connects to Merrill for execution.

## Safety Boundaries

- No auto-trading.
- No broker login scraping.
- No brokerage credential storage.
- Merrill holdings are uploaded manually, and any trade execution remains manual.
- The preference engine is decision support only; it does not place, route, or stage orders.
- Portfolio intelligence is also decision support only; it does not automate trades or connect to Merrill for execution.
- API keys live only in `.env`.
- Earnings-before-expiration trades are rejected.
- Wide spreads, low volume, low open interest, and assignment risk are surfaced clearly.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and add your Massive API key for the preferred personal-beta provider:

```bash
OPTIONS_PROVIDER=auto
MASSIVE_API_KEY=your_key_here
MASSIVE_DATA_ENTITLEMENT=unknown
```

`POLYGON_API_KEY` is also accepted as a backward-compatible alias. To use Tradier instead:

```bash
OPTIONS_PROVIDER=tradier
TRADIER_ENV=live
TRADIER_ACCESS_TOKEN=your_token_here
TRADIER_DATA_ENTITLEMENT=unknown
```

For a no-key local demo, choose `Demo` in the app or set:

```bash
OPTIONS_PROVIDER=demo
```

Run the dashboard:

```bash
streamlit run streamlit_app.py
```

## Deploy To Streamlit Community Cloud

Streamlit Community Cloud deploys only from GitHub. Put this folder in a GitHub repository, push the current branch, then create a Streamlit app from that repo.

In Streamlit Cloud, add secrets under app settings:

```toml
OPTIONS_PROVIDER = "tradier"
TRADIER_ENV = "live"
TRADIER_ACCESS_TOKEN = "your_token_here"
```

Do not commit `.env`; it is ignored for safety.

## Holdings CSV Format

The parser accepts common Merrill-style names as long as it can find:

- a symbol column: `Symbol`, `Ticker`, or `Security Symbol`
- a share column: `Quantity`, `Qty`, or `Shares`

Optional columns:

- `Cost Basis`
- `Account`

See `data/sample_holdings.csv`.

## Default Portfolio Tiers

Tier 1 core compounders:

`TQQQ`, `NVDA`, `GOOG`, `AMZN`

Rule: covered calls only surface when premium is unusually attractive.

Tier 2 volatility harvest:

`MU`, `AEHR`, `NBIS`, `IREN`, `CRWD`, `DELL`, `AMD`, `SMH`, `SOXL`, `RKLB`, `LUNR`

Rule: calls and puts can rank well when risk/reward, liquidity, and spread quality are strong.

## Screening Logic

Covered calls:

- Scans only tickers where the holdings CSV shows at least 100 owned shares.
- Requires at least 100 owned shares per contract.
- Uses calls above current price.
- Uses delta as an assignment probability proxy.
- Assignment outcome is shown as shares that may be sold at the strike.

Cash-secured puts:

- Scans every ticker already owned plus additional approved watchlist tickers.
- Assignment obligation must be less than or equal to available cash.
- Uses puts below current price.
- Shows effective entry price as strike minus midpoint premium.
- Assignment outcome is shown as shares that may be purchased at the strike and the effective entry after premium.

Filters:

- Target delta defaults to `0.03` through `0.10`.
- Minimum premium yield defaults to `0.30%` weekly.
- Maximum bid/ask spread defaults to `25%`.
- Earnings before expiration are rejected.

Ranking:

- Candidates are ranked by Premium Efficiency Score.
- Premium Efficiency Score = premium collected x IV Rank / assignment probability / capital required.
- Ticker preferences adjust the score after premium efficiency is calculated.
- If a provider does not supply IV Rank, the engine uses a neutral `1.0` value so candidates can still be ranked.

## John Preference Engine

Ticker-level preferences live in `data/ticker_profiles.json`.

Each profile supports:

- `ticker`
- `tier`
- `category`
- `own_more_score`: 1 through 5
- `happy_to_sell_score`: 1 through 5
- `max_contracts`
- `notes`

Default profile categories include:

- Core Compounder
- Semiconductor
- AI Infrastructure
- Cybersecurity
- Crypto Infrastructure
- Space
- Energy
- Cash
- Other

Preference rules:

- Covered calls are penalized when `happy_to_sell_score` is low.
- Cash-secured puts are boosted when `own_more_score` is high.
- `max_contracts` caps both covered-call and cash-secured-put sizing for that ticker.
- To use a different JSON profile file, set `TICKER_PROFILES_PATH`.

## Portfolio Intelligence

The engine uses holdings, current prices, cash balance, and ticker profiles to calculate portfolio exposure before ranking trades.

For each holding, the portfolio model calculates:

- ticker
- shares
- current price
- market value
- profile category
- portfolio weight percentage

The dashboard shows:

- total portfolio market value
- cash balance
- category exposure percentages
- top 10 ticker exposures
- largest single-name concentration
- largest category concentration

Option exposure is simulated before recommendations are ranked.

Covered calls calculate:

- shares currently owned
- shares covered
- shares remaining if called away
- market value at risk of being sold
- category exposure reduction if called away

Cash-secured puts calculate:

- cash required
- shares acquired if assigned
- effective entry price
- new position value if assigned
- category exposure increase if assigned

Portfolio risk alerts include:

- single ticker exposure above 20%
- category exposure above 40%
- put assignment using more than 50% of available cash
- covered call covering more than 50% of owned shares
- assignment materially increasing already-high category exposure
- selling calls materially reducing exposure to a core compounder

These alerts adjust scoring so the engine penalizes trades that create excessive ticker/category concentration, use too much cash, or sell too much of a core compounder. Trades that generate premium while keeping concentration balanced can receive a modest boost.

## Executive Dashboard

The first screen after running a scan summarizes the current opportunity set before the full candidate table.

The dashboard shows:

- total portfolio value
- cash available
- candidate count
- Sell recommendation count
- best covered-call Premium Efficiency Score
- best cash-secured-put Premium Efficiency Score
- total premium available from Sell-rated candidates
- total cash required if all Sell-rated puts are assigned

The Best Opportunities section highlights:

- best covered call
- best cash-secured put
- best overall trade

Each opportunity shows the ticker, strategy, expiration, strike, premium, Premium Efficiency Score, estimated assignment probability, assignment outcome, portfolio risk alerts, and a short explanation of why it ranks highly.

The Income Forecast section is a current-screen opportunity view only. It is not historical monthly income, realized income, or a trade journal.

- Conservative: premium from the top 3 Sell-rated candidates.
- Expected: premium from all Sell-rated candidates.
- Aggressive: premium from Sell and Maybe candidates.

The Risk Alerts section aggregates portfolio concentration warnings, cash-use warnings, covered-call risks on core compounders, liquidity warnings, wide spread warnings, and earnings warnings when present.

The full ranked candidate table remains below the dashboard and includes filters for strategy, recommendation, ticker search, and minimum Premium Efficiency Score.

The Executive Dashboard is decision support only. It does not place trades, stage orders, log in to brokerages, or store trading history.

## Output Columns

The ranked table includes ticker, strategy, expiration, strike, current price, bid, ask, mid, delta, IV Rank, estimated assignment probability, Premium Efficiency Score, premium per contract, total premium, shares covered, cash required, capital at risk, assignment outcome, effective entry price, percent out-of-the-money, weekly yield, annualized yield, liquidity warning, earnings warning, recommendation, suggested limit price, tier, category, own more score, happy to sell score, max contracts, profile notes, preference adjustment, current ticker weight, current category weight, post-assignment ticker weight, post-assignment category weight, cash used if assigned, shares remaining if called away, portfolio risk alerts, portfolio risk adjustment, score, and contract count.

## Data Providers

Supported providers:

- Massive: preferred personal-beta provider for real-time REST snapshots, underlying quotes, option-chain snapshots, bid/ask, Greeks, implied volatility, volume, and open interest. Configure with `MASSIVE_API_KEY`. `POLYGON_API_KEY` is accepted for backward compatibility.
- Tradier: fallback provider for option chains with bid/ask, Greeks, IV, volume, and open interest. Configure with `TRADIER_ACCESS_TOKEN`.
- Demo: local sample data only. Demo mode is explicit and is never used as a hidden fallback for live portfolio runs.

Provider selection:

- `OPTIONS_PROVIDER=auto` selects Massive when `MASSIVE_API_KEY` exists, otherwise Tradier when `TRADIER_ACCESS_TOKEN` exists.
- The Streamlit sidebar also shows a compact provider selector.
- The dashboard shows which provider supplied data, whether it is real-time or delayed/demo, provider health, and the last successful refresh.
- The Market Data Status section includes a `Test Market Data Connection` button that checks authentication, equity quotes, option expirations, option chains, bid/ask quotes, Greeks, timestamps, and real-time/delayed/unknown status.
- Data is labeled `Unknown` unless real-time or delayed entitlement is explicit from provider metadata or local entitlement configuration. Do not set `MASSIVE_DATA_ENTITLEMENT=real-time` or `TRADIER_DATA_ENTITLEMENT=real-time` unless your plan is confirmed.

Freshness rules:

- Equity quotes are stale after 30 seconds during market hours.
- Option quotes are stale after 30 seconds during market hours.
- Option-chain snapshots are stale after 60 seconds during market hours.
- Earnings dates and historical volatility should be refreshed daily.
- Outside market hours, stale checks account for the market being closed.

Stale-data behavior:

- The scorer refuses stale option contracts and contracts with missing or zero bid/ask.
- Live runs do not silently fall back to Demo data.
- Delayed or stale status is shown in the app so users do not need Terminal logs to understand data quality.
- Raw implied volatility is displayed as `IV`. It is separate from `IV Rank`.
- If true IV Rank is unavailable, the Premium Efficiency calculation uses a neutral multiplier and candidates show `IV rank unavailable from selected provider.`

Real-time availability depends on the user’s provider subscription. Tradier sandbox data is treated as delayed. Massive real-time option-chain snapshots require a plan that includes real-time options data.

Developer note:

- Provider adapters implement the normalized `MarketDataProvider` interface in `options_income_engine/providers.py`.
- Normalized market-data objects live in `options_income_engine/market_data.py` and carry provider, timestamps, stale flags, source feed, request status, raw symbol, and normalized symbol.
