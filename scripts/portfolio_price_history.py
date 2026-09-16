#!/usr/bin/env python3
"""
Portfolio historical price & drawdown report.

Pulls split/dividend-adjusted daily closing prices from Yahoo Finance (via the
`yfinance` package) for a fixed list of tickers and computes, for each one:

  * current price (most recent close in the series)
  * price 1 week / 1 month / 3 months / 6 months ago
  * the highest daily close in the trailing 6-month window, and its date
  * % change vs. each of those look-back points
  * % drawdown vs. the 6-month peak

Writes `portfolio_price_history.csv` and prints the same table to stdout,
sorted by drawdown (worst first).

Usage:
    pip install yfinance
    python3 portfolio_price_history.py
    python3 portfolio_price_history.py --out my.csv --chart drawdown.png

Every ticker always gets exactly one output row. A ticker that cannot be
fetched, or that has no data for a given look-back date, is reported with
blank/labelled fields and an explanatory note -- prices are never fabricated,
interpolated or carried forward across a gap in history.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

# --------------------------------------------------------------------------
# Portfolio
# --------------------------------------------------------------------------

# Order is significant: the CSV is emitted in this order.
TICKERS: list[str] = [
    "NVDA", "MSFT", "MRNA", "AMZN", "AAPL", "RKLB", "GOOG", "GLW", "MELI",
    "ASML", "VRTX", "KARO", "MOG.A", "GOOGL", "TMDX", "KRYS", "TSLA", "MDT",
    "NU", "FPS", "SHOP", "OC", "TKO", "MRVL", "CDNS", "PLD", "META", "SYM",
    "AVGO", "SBGSY", "UTHR", "IBM", "INTC", "MLI", "RDDT", "KBR", "NBIS",
    "WRBY", "CBRS", "BWXT", "HON", "HONA",
]

# Look-back offsets, in calendar days back from the most recent trading date.
# The nearest *prior* trading day is used when the exact date is not a
# trading day.
PERIODS: list[tuple[str, int]] = [
    ("1w", 7),
    ("1m", 30),
    ("3m", 91),
    ("6m", 182),
]

SIX_MONTH_DAYS = 182


@dataclass(frozen=True)
class TickerInfo:
    """Known corporate-action context for a ticker.

    `listing_date`   -- first regular-way trading date (IPO or spin-off).
    `listing_label`  -- what to put in a price cell that predates the listing,
                        e.g. "N/A - pre-IPO".
    `short_history_uses_first_close` -- when True, a look-back date that falls
                        before the listing date resolves to the first available
                        close ("since IPO") instead of being reported as N/A.
    `reverse_split`  -- (date, ratio) of a known split, used to sanity-check
                        that yfinance's adjustment was actually applied.
    """

    listing_date: date | None = None
    listing_label: str = ""
    listing_note: str = ""
    short_history_uses_first_close: bool = False
    reverse_split: tuple[date, float] | None = None
    extra_note: str = ""


TICKER_INFO: dict[str, TickerInfo] = {
    "HONA": TickerInfo(
        listing_date=date(2026, 6, 29),
        listing_label="N/A - pre-spinoff",
        listing_note="spinoff Jun 29 2026 - limited history",
    ),
    "CBRS": TickerInfo(
        listing_date=date(2026, 5, 14),
        listing_label="N/A - pre-IPO",
        listing_note="IPO May 14 2026 - limited history",
    ),
    "FPS": TickerInfo(
        listing_date=date(2026, 2, 5),
        listing_label="N/A - pre-IPO",
        listing_note="IPO Feb 5 2026",
        short_history_uses_first_close=True,
    ),
    "HON": TickerInfo(
        reverse_split=(date(2026, 6, 29), 2.0),
        extra_note="1-for-2 reverse split Jun 29 2026",
    ),
}

# yfinance wants a hyphen where the exchange uses a dot (MOG.A -> MOG-A).
# Candidates are tried in order and the first one returning data wins.
def symbol_candidates(ticker: str) -> list[str]:
    candidates = [ticker]
    if "." in ticker:
        candidates.append(ticker.replace(".", "-"))
    return candidates


CSV_COLUMNS = [
    "ticker", "current_price", "price_1w_ago", "price_1m_ago", "price_3m_ago",
    "price_6m_ago", "peak_6m", "peak_6m_date", "pct_chg_1w", "pct_chg_1m",
    "pct_chg_3m", "pct_chg_6m", "pct_drawdown_from_peak", "notes",
]


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def _extract_close(df: "pd.DataFrame") -> "pd.Series | None":
    """Pull a single adjusted-close series out of whatever shape yfinance returned.

    yf.download returns MultiIndex columns (field, ticker) for a single ticker
    in recent versions and flat columns in older ones; group_by can also swap
    the levels. Search both.
    """
    if df is None or len(df) == 0:
        return None

    close = None
    if isinstance(df.columns, pd.MultiIndex):
        for level in range(df.columns.nlevels):
            if "Close" in df.columns.get_level_values(level):
                close = df.xs("Close", axis=1, level=level)
                break
    elif "Close" in df.columns:
        close = df["Close"]

    if close is None:
        return None
    if isinstance(close, pd.DataFrame):
        if close.shape[1] == 0:
            return None
        close = close.iloc[:, 0]

    # Normalise the index to naive midnight timestamps so date arithmetic and
    # label-based slicing behave predictably.
    idx = pd.DatetimeIndex(close.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    close = pd.Series(close.to_numpy(), index=idx.normalize(), name="Close")

    close = pd.to_numeric(close, errors="coerce").dropna()
    close = close[close > 0]
    close = close[~close.index.duplicated(keep="last")].sort_index()
    return close if len(close) else None


class _YFLogCapture(logging.Handler):
    """Capture yfinance's own log output.

    yfinance swallows network/HTTP errors internally -- it logs them and
    returns an empty frame rather than raising -- so without this the only
    error available is an unhelpful "no price rows returned". Capturing the
    log lets the CSV note the real cause (rate limit, proxy denial, delisting).
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(record.getMessage().strip())
        except Exception:                             # noqa: BLE001
            pass

    def drain(self) -> str:
        """Return captured messages as one de-duplicated line, then reset."""
        seen, unique = set(), []
        for m in self.messages:
            m = " ".join(m.split())
            if m and m not in seen:
                seen.add(m)
                unique.append(m)
        self.messages.clear()
        return " | ".join(unique)


_YF_LOG = _YFLogCapture()


def _attach_yf_logger() -> None:
    logger = logging.getLogger("yfinance")
    if _YF_LOG not in logger.handlers:
        logger.addHandler(_YF_LOG)
        logger.setLevel(logging.WARNING)


def fetch_close_series(symbol: str, delay: float) -> "pd.Series":
    """Download ~7 months of adjusted daily closes for one exact symbol.

    Tries period="7mo" first (per spec: 6 months plus a buffer so the exact
    6-months-ago calendar date always has a prior trading day available), then
    falls back to an explicit start/end range, since not every Yahoo endpoint
    accepts a 7-month range string. Raises on failure.
    """
    import yfinance as yf

    _attach_yf_logger()
    _YF_LOG.drain()          # discard anything left over from a prior symbol

    attempts: list[dict] = [
        {"period": "7mo"},
        {"start": (date.today() - timedelta(days=225)).isoformat(),
         "end": (date.today() + timedelta(days=1)).isoformat()},
    ]

    last_error: Exception | None = None
    for i, kwargs in enumerate(attempts):
        if i:
            time.sleep(delay)
        try:
            df = yf.download(
                symbol,
                interval="1d",
                auto_adjust=True,      # split/dividend adjusted closes
                progress=False,
                actions=False,
                threads=False,
                **kwargs,
            )
        except Exception as exc:                      # noqa: BLE001
            last_error = exc
            continue
        close = _extract_close(df)
        if close is not None:
            _YF_LOG.drain()
            return close
        detail = _YF_LOG.drain()
        last_error = ValueError(detail or "no price rows returned")

    raise RuntimeError(str(last_error) if last_error else "no data returned")


def resolve_and_fetch(ticker: str, delay: float) -> tuple["pd.Series", str, list[str]]:
    """Resolve a portfolio ticker to a working yfinance symbol and fetch it.

    Returns (close series, symbol actually used, notes). Raises RuntimeError
    with every attempted symbol's error if none of the candidates work.
    """
    notes: list[str] = []
    errors: list[str] = []
    for symbol in symbol_candidates(ticker):
        try:
            close = fetch_close_series(symbol, delay)
        except Exception as exc:                      # noqa: BLE001
            errors.append(f"{symbol}: {exc}")
            time.sleep(delay)
            continue
        if symbol != ticker:
            notes.append(f"ticker resolved as {symbol} not {ticker}")
        return close, symbol, notes
    raise RuntimeError("; ".join(errors) or "no symbol candidates succeeded")


# --------------------------------------------------------------------------
# Calculations
# --------------------------------------------------------------------------

def close_on_or_before(close: "pd.Series", target: date) -> tuple[float, date] | None:
    """Close for `target`, or the nearest *prior* trading day. None if the
    series starts after `target` (never interpolated, never carried backwards)."""
    window = close.loc[:pd.Timestamp(target)]
    if len(window) == 0:
        return None
    return float(window.iloc[-1]), window.index[-1].date()


def pct_change(current: float, past: float) -> float | None:
    if past is None or current is None or past == 0:
        return None
    return (current - past) / past * 100.0


def detect_stale_run(close: "pd.Series") -> int:
    """Longest run of consecutive identical closes -- a smell for thin ADRs
    where Yahoo repeats the last trade rather than reporting a real close."""
    longest = run = 1
    values = close.to_numpy()
    for i in range(1, len(values)):
        run = run + 1 if values[i] == values[i - 1] else 1
        longest = max(longest, run)
    return longest if len(values) else 0


def detect_split_artifact(close: "pd.Series", split_date: date, ratio: float) -> bool:
    """True if an unadjusted split jump is still visible around `split_date`.

    With auto_adjust=True the pre-split closes should already be divided by the
    ratio, so no ~ratio-sized single-day gap should remain. If one does, the
    adjustment did not land and the older prices are off by that factor.
    """
    lo = pd.Timestamp(split_date) - pd.Timedelta(days=7)
    hi = pd.Timestamp(split_date) + pd.Timedelta(days=7)
    window = close.loc[lo:hi]
    if len(window) < 2:
        return False
    steps = window.to_numpy()
    for i in range(1, len(steps)):
        if steps[i - 1] == 0:
            continue
        move = steps[i] / steps[i - 1]
        if abs(move - ratio) < 0.15 or abs(move - 1.0 / ratio) < 0.15 / ratio:
            return True
    return False


def detect_price_anomaly(close: "pd.Series") -> str | None:
    """Flag any single-day move above 40%, which usually means an
    unadjusted corporate action rather than a real move."""
    values = close.to_numpy()
    for i in range(1, len(values)):
        if values[i - 1] <= 0:
            continue
        move = values[i] / values[i - 1] - 1.0
        if abs(move) > 0.40:
            return (f"check {close.index[i].date()}: single-day move "
                    f"{move * 100:+.0f}% - possible unadjusted corporate action")
    return None


@dataclass
class Row:
    ticker: str
    values: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    drawdown: float | None = None       # numeric, for sorting only
    current_date: date | None = None    # most recent trading day for this ticker

    def as_csv(self) -> dict[str, object]:
        out = {c: self.values.get(c, "") for c in CSV_COLUMNS}
        out["ticker"] = self.ticker
        out["notes"] = "; ".join(n for n in self.notes if n)
        return out


def build_row(ticker: str, close: "pd.Series", notes: list[str]) -> Row:
    info = TICKER_INFO.get(ticker, TickerInfo())
    row = Row(ticker=ticker, notes=list(notes))
    vals: dict[str, object] = {}

    current_ts = close.index[-1]
    current_date = current_ts.date()
    current = float(close.iloc[-1])
    first_date = close.index[0].date()
    vals["current_price"] = round(current, 2)
    row.current_date = current_date

    # --- look-back prices -------------------------------------------------
    for label, days in PERIODS:
        target = current_date - timedelta(days=days)
        found = close_on_or_before(close, target)

        if found is None:
            # No trading day at or before the target: history starts later.
            if info.short_history_uses_first_close:
                past = float(close.iloc[0])
                vals[f"price_{label}_ago"] = round(past, 2)
                vals[f"pct_chg_{label}"] = round(pct_change(current, past), 1)
                row.notes.append(
                    f"{label} look-back predates listing {info.listing_date} - "
                    f"using first close {first_date} (since IPO)")
                continue
            if info.listing_date is not None:
                vals[f"price_{label}_ago"] = info.listing_label
                vals[f"pct_chg_{label}"] = info.listing_label
            else:
                vals[f"price_{label}_ago"] = ""
                vals[f"pct_chg_{label}"] = ""
                row.notes.append(
                    f"no data for {label} look-back ({target}); "
                    f"history starts {first_date}")
            continue

        past, past_date = found
        vals[f"price_{label}_ago"] = round(past, 2)
        change = pct_change(current, past)
        vals[f"pct_chg_{label}"] = round(change, 1) if change is not None else ""

    # --- 6-month peak and drawdown ---------------------------------------
    window_start = pd.Timestamp(current_date - timedelta(days=SIX_MONTH_DAYS))
    window = close.loc[window_start:]
    if len(window) == 0:
        window = close
    peak = float(window.max())
    peak_date = window.idxmax().date()
    vals["peak_6m"] = round(peak, 2)
    vals["peak_6m_date"] = peak_date.isoformat()

    drawdown = pct_change(current, peak)
    vals["pct_drawdown_from_peak"] = round(drawdown, 1)
    row.drawdown = drawdown

    if close.index[0] > window_start:
        row.notes.append(f"peak over partial history (from {first_date})")

    # --- sanity checks ----------------------------------------------------
    if info.listing_note:
        row.notes.append(info.listing_note)
    if info.extra_note:
        row.notes.append(info.extra_note)

    if info.reverse_split is not None:
        split_date, ratio = info.reverse_split
        if detect_split_artifact(close, split_date, ratio):
            row.notes.append(
                f"WARNING: ~{ratio:g}x jump still present around {split_date} - "
                f"reverse split may NOT be adjusted; pre-split prices suspect")

    stale = detect_stale_run(close)
    if stale >= 3:
        row.notes.append(f"possible stale quotes: {stale} identical consecutive closes")

    anomaly = detect_price_anomaly(close)
    if anomaly and not any("reverse split" in n for n in row.notes):
        row.notes.append(anomaly)

    row.values = vals
    return row


def failed_row(ticker: str, error: str) -> Row:
    row = Row(ticker=ticker)
    row.values = {c: "" for c in CSV_COLUMNS}
    row.notes.append(f"yfinance fetch failed: {error}")
    return row


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def print_table(rows: list[Row]) -> None:
    """Print the rows sorted by drawdown ascending (worst hit first).
    Rows without a drawdown (failed fetches) sort last."""
    ordered = sorted(
        rows,
        key=lambda r: (r.drawdown is None, r.drawdown if r.drawdown is not None else 0.0),
    )
    table = [{k: ("" if v is None else str(v)) for k, v in r.as_csv().items()}
             for r in ordered]

    widths = {c: max(len(c), *(len(t[c]) for t in table)) if table else len(c)
              for c in CSV_COLUMNS}
    # Keep the notes column from blowing out the terminal.
    widths["notes"] = min(widths["notes"], 60)

    def fmt(cells: dict[str, str]) -> str:
        parts = []
        for c in CSV_COLUMNS:
            text = cells[c]
            if c == "notes" and len(text) > widths["notes"]:
                text = text[: widths["notes"] - 1] + "…"
            parts.append(text.ljust(widths[c]) if c in ("ticker", "peak_6m_date", "notes")
                         else text.rjust(widths[c]))
        return "  ".join(parts).rstrip()

    header = fmt({c: c for c in CSV_COLUMNS})
    print(header)
    print("-" * len(header))
    for cells in table:
        print(fmt(cells))


def write_csv(rows: list[Row], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:           # portfolio order, not sorted order
            writer.writerow(row.as_csv())


def write_chart(rows: list[Row], path: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                          # noqa: BLE001
        print(f"\n[chart skipped: matplotlib unavailable ({exc})]", file=sys.stderr)
        return

    plotted = sorted(
        [r for r in rows if r.drawdown is not None], key=lambda r: r.drawdown
    )
    if not plotted:
        print("\n[chart skipped: no drawdown data]", file=sys.stderr)
        return

    labels = [r.ticker for r in plotted]
    values = [r.drawdown for r in plotted]
    fig, ax = plt.subplots(figsize=(max(8.0, len(labels) * 0.32), 6.0))
    ax.bar(labels, values, color="#c0392b")
    ax.set_ylabel("% drawdown from 6-month peak")
    ax.set_title("Drawdown from trailing 6-month peak (worst to best)")
    ax.axhline(0, color="#333333", linewidth=0.8)
    ax.tick_params(axis="x", labelrotation=90, labelsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Chart written to {path}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def collect(tickers: list[str], delay: float) -> list[Row]:
    rows: list[Row] = []
    for i, ticker in enumerate(tickers):
        if i:
            time.sleep(delay)      # be polite; avoids Yahoo rate-limiting
        print(f"  [{i + 1:>2}/{len(tickers)}] {ticker} ...", end=" ", file=sys.stderr, flush=True)
        try:
            close, _symbol, notes = resolve_and_fetch(ticker, delay)
        except Exception as exc:                      # noqa: BLE001
            print(f"FAILED ({exc})", file=sys.stderr)
            rows.append(failed_row(ticker, str(exc).replace("\n", " ")[:200]))
            continue
        try:
            row = build_row(ticker, close, notes)
        except Exception as exc:                      # noqa: BLE001
            print(f"FAILED (calc: {exc})", file=sys.stderr)
            rows.append(failed_row(ticker, f"calculation error: {exc}"))
            continue
        print(f"ok ({len(close)} closes, last {close.index[-1].date()})", file=sys.stderr)
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="portfolio_price_history.csv",
                        help="output CSV path (default: portfolio_price_history.csv)")
    parser.add_argument("--chart", nargs="?", const="portfolio_drawdown.png", default=None,
                        help="also write a drawdown bar chart to this PNG path")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="seconds to sleep between requests (default: 0.5)")
    parser.add_argument("--tickers", default=None,
                        help="comma-separated ticker override (default: the 42-name portfolio)")
    args = parser.parse_args(argv)

    tickers = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()]
               if args.tickers else list(TICKERS))

    print(f"Run started: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Tickers requested: {len(tickers)}")
    print("Fetching daily adjusted closes from Yahoo Finance via yfinance "
          "(period=7mo, auto_adjust=True)...\n", file=sys.stderr)

    rows = collect(tickers, args.delay)

    # Confirm what "current" means -- every look-back is measured from this date.
    ok = [r for r in rows if r.current_date is not None]
    failures = [r for r in rows if r.current_date is None]

    print()
    print(f"Rows with data: {len(ok)} / {len(rows)}")
    if ok:
        dates = sorted({r.current_date for r in ok})
        latest = dates[-1]
        print(f'"Current" = most recent trading day in the data: {latest} '
              f'(1w/1m/3m/6m are measured back from each ticker\'s own latest close)')
        stragglers = [r for r in ok if r.current_date != latest]
        if stragglers:
            print(f"  Note: {len(stragglers)} ticker(s) have an older last close "
                  f"(possible halt/delisting/stale feed): "
                  + ", ".join(f"{r.ticker}={r.current_date}" for r in stragglers))
    if failures:
        print(f"Rows that failed to fetch: {len(failures)} "
              f"({', '.join(r.ticker for r in failures)})")
    print()

    print_table(rows)
    write_csv(rows, args.out)
    print(f"\nCSV written to {args.out}")
    if args.chart:
        write_chart(rows, args.chart)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
