# Options Income Engine

A local Streamlit app that screens a Merrill holdings CSV and an approved watchlist for weekly covered calls and cash-secured puts. It ranks candidate trades only; it never submits orders.

## Safety Boundaries

- No auto-trading.
- No broker login scraping.
- No brokerage credential storage.
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

Edit `.env` and add your Tradier token:

```bash
OPTIONS_PROVIDER=tradier
TRADIER_ENV=sandbox
TRADIER_ACCESS_TOKEN=your_token_here
```

For a no-key local demo:

```bash
OPTIONS_PROVIDER=mock
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

- Requires at least 100 owned shares per contract.
- Uses calls above current price.
- Uses delta as an assignment probability proxy.

Cash-secured puts:

- Uses approved watchlist tickers only.
- Assignment obligation must be less than or equal to available cash.
- Uses puts below current price.
- Shows effective entry price as strike minus midpoint premium.

Filters:

- Target delta defaults to `0.03` through `0.10`.
- Minimum premium yield defaults to `0.30%` weekly.
- Maximum bid/ask spread defaults to `25%`.
- Earnings before expiration are rejected.

## Output Columns

The ranked table includes ticker, strategy, expiration, strike, current price, bid, ask, mid, delta, estimated assignment probability, premium per contract, total premium, capital at risk or shares covered, effective entry price, percent out-of-the-money, weekly yield, annualized yield, liquidity warning, earnings warning, recommendation, suggested limit price, tier, score, and contract count.

## Data Providers

Primary supported provider:

- Tradier: options chains with bid/ask, Greeks, volume, and open interest.

Fallback:

- yfinance is used only for stock prices and earnings dates when needed. It is not used as the primary source for options Greeks.

Reserved:

- Polygon and Interactive Brokers can be added by implementing the `MarketDataProvider` interface in `options_income_engine/providers.py`.
