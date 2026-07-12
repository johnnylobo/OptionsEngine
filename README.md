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

## Easy Mode

On macOS, double-click `run_app.command` to start the app. The first run may take a minute because it creates the local Python environment and installs the app requirements.

You can also run one command from this folder:

```bash
./run_app.command
```

Once the app opens:

- Upload your Merrill holdings CSV.
- The app imports Merrill holdings automatically in normal use, including exports with account summaries before the holdings table.
- After import, confirm the parsed holdings preview, total portfolio value, and number of tickers with 100+ shares, then click `Screen candidates`.
- If the app cannot find the holdings table automatically, use the Holdings Import Wizard once to pick the header row and map Symbol and Quantity/Shares. You can save that as the Merrill default mapping for future uploads.
- If you do not have a CSV handy, click `Use Demo Portfolio` to explore the dashboard with sample holdings and mock market data.

You do not need to clean Merrill CSV files manually. The import flow is designed to handle account summaries, blank rows, quoted column names, trailing spaces, and share quantities with commas.

## Manual Setup

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

If automatic import cannot identify the holdings table, the app first tries your saved Merrill default mapping if one exists. If that also cannot import holdings, the app opens a Holdings Import Wizard instead of stopping. The wizard previews the first 100 CSV rows, lets you choose the real header row, and lets you map Symbol and Quantity/Shares columns manually, with optional Price, Market Value, and Description columns. This is designed for messy brokerage exports with account summaries, disclaimers, blank rows, repeated section headers, quoted headers, and trailing spaces in column names.

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

## Output Columns

The ranked table includes ticker, strategy, expiration, strike, current price, bid, ask, mid, delta, IV Rank, estimated assignment probability, Premium Efficiency Score, premium per contract, total premium, shares covered, cash required, capital at risk, assignment outcome, effective entry price, percent out-of-the-money, weekly yield, annualized yield, liquidity warning, earnings warning, recommendation, suggested limit price, tier, category, own more score, happy to sell score, max contracts, profile notes, preference adjustment, current ticker weight, current category weight, post-assignment ticker weight, post-assignment category weight, cash used if assigned, shares remaining if called away, portfolio risk alerts, portfolio risk adjustment, score, and contract count.

## Data Providers

Primary supported provider:

- Tradier: options chains with bid/ask, Greeks, volume, and open interest.

Fallback:

- yfinance is used only for stock prices and earnings dates when needed. It is not used as the primary source for options Greeks.

Reserved:

- Polygon and Interactive Brokers can be added by implementing the `MarketDataProvider` interface in `options_income_engine/providers.py`.
