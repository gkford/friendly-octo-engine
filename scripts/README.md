# scripts/

Standalone utilities. These are unrelated to the Lava Runner game itself.

## portfolio_price_history.py

Pulls verified historical daily closing prices for a 42-ticker portfolio from
Yahoo Finance and reports look-back changes and drawdown from the trailing
6-month peak.

```bash
pip install yfinance            # matplotlib only needed for --chart
python3 scripts/portfolio_price_history.py
```

Writes `portfolio_price_history.csv` and prints the same table to stdout,
sorted by `pct_drawdown_from_peak` ascending (worst-hit holdings first).

### Options

| Flag | Default | Purpose |
|---|---|---|
| `--out PATH` | `portfolio_price_history.csv` | CSV output path |
| `--chart [PATH]` | off (`portfolio_drawdown.png` if bare) | Also write a drawdown bar chart |
| `--delay SECONDS` | `0.5` | Sleep between requests, to avoid Yahoo rate-limiting |
| `--tickers A,B,C` | the 42-name portfolio | Override the ticker list |

### Behaviour

* Prices are **split/dividend adjusted** (`auto_adjust=True`).
* History is pulled with `period="7mo"` — six months plus a buffer, so the
  exact 6-months-ago calendar date always has a prior trading day available.
  If that range string is rejected, it retries once with an explicit
  start/end range.
* Look-backs are 7 / 30 / 91 / 182 calendar days before each ticker's most
  recent close, resolving to the **nearest prior trading day**.
* Prices round to 2dp, percentages to 1dp.
* Dotted symbols are retried in hyphen form (`MOG.A` → `MOG-A`) and the
  substitution is recorded in `notes`.

### Reliability

* **Every ticker always gets exactly one row.** A fetch failure produces a row
  with blank prices and `yfinance fetch failed: <reason>` in `notes`. yfinance
  swallows network errors internally, so its log output is captured to make
  the real cause (rate limit, proxy denial, delisting) visible in that note.
* **Prices are never fabricated.** A look-back date with no data is left blank,
  or labelled `N/A - pre-IPO` / `N/A - pre-spinoff` where the cause is a known
  listing date. Nothing is interpolated or carried backwards across a gap.
* Short-history tickers still get a peak and drawdown, computed over whatever
  history exists, with `peak over partial history (from <date>)` in `notes`.

### Automatic sanity checks

These write warnings into `notes` rather than failing the run:

* **Unapplied splits** — flags a residual ~2x single-day gap around a known
  split date, which would mean `auto_adjust` did not land and the pre-split
  prices are wrong by that factor (checked for HON's 1-for-2 reverse split of
  2026-06-29).
* **Stale quotes** — flags 3 or more identical consecutive closes, the usual
  smell for thinly traded ADRs such as SBGSY.
* **Price anomalies** — flags any unexplained single-day move above 40%.
* **Lagging tickers** — the run header calls out any ticker whose most recent
  close is older than the rest of the portfolio.

Known listing dates are declared in `TICKER_INFO`; the script derives what
data actually exists from the series itself rather than assuming, so the
labels stay correct as the run date moves.

## fetch_prices.py

Refreshes the live dashboard in `docs/`. Pulls the intraday price, previous
close and ~7 months of adjusted daily closes for all 42 holdings, plus the
USD/NZD rate, and writes `docs/data.json`.

```bash
pip install yfinance
python3 scripts/fetch_prices.py               # one refresh
python3 scripts/fetch_prices.py --watch 900   # refresh every 15 minutes
```

See [`docs/README.md`](../docs/README.md) for the dashboard, deployment, and how
to edit the good-buy ratings.

Holdings (as shares per owner), good-buy ratings and corporate-action context
are declared in the configuration block at the top of the script. History is fetched for all
tickers in one batched request (with per-ticker retry for anything the batch
misses) to stay well clear of Yahoo's rate limits at a 20-minute cadence.

This supersedes `portfolio_price_history.py` for day-to-day use; that script is
kept because it still produces the one-off CSV and drawdown chart.
