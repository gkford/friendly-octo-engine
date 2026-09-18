#!/usr/bin/env python3
"""
Refresh portfolio price data for the dashboard.

Pulls, for all 42 holdings: the live/most-recent intraday price, the previous
close, and ~7 months of split/dividend-adjusted daily closes (for the 1w/1m/3m/6m
look-backs and the trailing 6-month peak). Also pulls the USD/NZD rate. Writes
everything to docs/data.json, which docs/index.html renders client-side.

Usage:
    pip install yfinance
    python3 scripts/fetch_prices.py                 # one refresh
    python3 scripts/fetch_prices.py --watch 900     # refresh every 15 minutes

Reliability contract:
  * One ticker's failure never aborts the refresh. A ticker that cannot be
    fetched keeps its last-known-good values from the existing data.json and is
    marked stale, rather than being blanked out.
  * If the FX fetch fails, the previous rate is carried forward and flagged
    stale.
  * Nothing is ever interpolated or estimated. A value that cannot be derived
    is written as null, which the dashboard renders as "N/A".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration -- edit these, not the logic below
# ---------------------------------------------------------------------------

# Owners, in the order their views appear in the dashboard's toggle.
OWNERS: list[str] = ["Graeme", "Tessa"]

# Which view the dashboard opens on: an owner name, or "Combined".
DEFAULT_VIEW = "Graeme"

# Placeholder owner for holdings whose per-owner split has not been supplied.
# While any holding still carries this, the dashboard can only show Combined.
UNATTRIBUTED = "Unattributed"

# Shares by owner, one entry per ticker.
#
#     "AMZN": {"Graeme": 14.9084},              held by Graeme only
#     "GLW":  {"Tessa": 13.7832},               held by Tessa only
#     "NVDA": {"Graeme": 20.0, "Tessa": 11.2714},   held by both
#
# A ticker held by both carries BOTH entries and stays a single row: the
# Combined view sums the entries, so a jointly held position is counted once
# at its full size -- never dropped, never counted twice. That is the whole
# reason the split lives here rather than in two separate lists.
#
# The counts below come from the original 42-line brief, which was already a
# merge of Graeme's Sharesies holdings and Tessa's spreadsheet with no record
# of which came from where. They are therefore parked under UNATTRIBUTED
# rather than guessed at. Replace each one with the real split.
HOLDINGS: dict[str, dict[str, float]] = {
    "NVDA":  {UNATTRIBUTED: 31.2714},
    "MSFT":  {UNATTRIBUTED: 11.9168},
    "MRNA":  {UNATTRIBUTED: 32.8788},
    "AMZN":  {UNATTRIBUTED: 14.9084},
    "AAPL":  {UNATTRIBUTED: 8},
    "RKLB":  {UNATTRIBUTED: 39.0632},
    "GOOG":  {UNATTRIBUTED: 6},
    "GLW":   {UNATTRIBUTED: 13.7832},
    "MELI":  {UNATTRIBUTED: 1},
    "ASML":  {UNATTRIBUTED: 1},
    "VRTX":  {UNATTRIBUTED: 3},
    "KARO":  {UNATTRIBUTED: 22},
    "MOG.A": {UNATTRIBUTED: 4.0076},
    "GOOGL": {UNATTRIBUTED: 4.1315},
    "TMDX":  {UNATTRIBUTED: 16},
    "KRYS":  {UNATTRIBUTED: 4},
    "TSLA":  {UNATTRIBUTED: 3.7752},
    "MDT":   {UNATTRIBUTED: 14},
    "NU":    {UNATTRIBUTED: 90},
    "FPS":   {UNATTRIBUTED: 40},
    "SHOP":  {UNATTRIBUTED: 9},
    "OC":    {UNATTRIBUTED: 9},
    "TKO":   {UNATTRIBUTED: 6},
    "MRVL":  {UNATTRIBUTED: 5},
    "CDNS":  {UNATTRIBUTED: 4},
    "PLD":   {UNATTRIBUTED: 8},
    "META":  {UNATTRIBUTED: 1.5251},
    "SYM":   {UNATTRIBUTED: 24},
    "AVGO":  {UNATTRIBUTED: 3},
    "SBGSY": {UNATTRIBUTED: 16},
    "UTHR":  {UNATTRIBUTED: 2},
    "IBM":   {UNATTRIBUTED: 4},
    "INTC":  {UNATTRIBUTED: 10},
    "MLI":   {UNATTRIBUTED: 16},
    "RDDT":  {UNATTRIBUTED: 6},
    "KBR":   {UNATTRIBUTED: 25},
    "NBIS":  {UNATTRIBUTED: 4.2786},
    "WRBY":  {UNATTRIBUTED: 35},
    "CBRS":  {UNATTRIBUTED: 4.1645},
    "BWXT":  {UNATTRIBUTED: 5},
    "HON":   {UNATTRIBUTED: 2},
    "HONA":  {UNATTRIBUTED: 2},
}


def split_is_supplied() -> bool:
    """True once every holding is attributed to real owners.

    The dashboard refuses to show a per-owner view while this is False, rather
    than presenting a total that silently omits or duplicates somebody's shares.
    """
    return all(
        owner_shares and all(o != UNATTRIBUTED for o in owner_shares)
        for owner_shares in HOLDINGS.values()
    )


# Who rates what as a good buy. This is a view on the stock, not a record of
# who owns or watches it. Add names or tickers freely -- a ticker appearing in
# more than one list renders as "Both" (see resolve_good_buys). A third person
# would need one more colour slot in docs/index.html; see its legend note.
GOOD_BUYS: dict[str, list[str]] = {
    "Tessa": ["AMZN", "GLW", "MRVL", "INTC", "KBR", "HON", "RKLB"],
    "Graeme": ["RKLB", "NBIS"],
}

# FX: Yahoo's "{CCY}=X" convention quotes USD -> CCY, so NZD=X is NZD per 1 USD,
# which is what the dashboard wants (multiply a USD amount by it to get NZD).
# verify_fx_convention() checks this at runtime rather than trusting it.
FX_TICKER = "NZD=X"
FX_PLAUSIBLE = (1.2, 2.5)        # NZD per USD; anything outside is suspect

# Look-back offsets in calendar days, resolved to the nearest *prior* trading day.
PERIODS: list[tuple[str, int]] = [("1w", 7), ("1m", 30), ("3m", 91), ("6m", 182)]
SIX_MONTH_DAYS = 182

OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "data.json")


@dataclass(frozen=True)
class TickerInfo:
    """Known corporate-action context for a ticker."""
    listing_date: date | None = None
    listing_kind: str = ""            # "IPO" or "spinoff"
    listing_note: str = ""
    reverse_split: tuple[date, float] | None = None
    extra_note: str = ""
    thin: bool = False                # thinly traded -> stale-quote check matters


TICKER_INFO: dict[str, TickerInfo] = {
    "HONA": TickerInfo(listing_date=date(2026, 6, 29), listing_kind="spinoff",
                       listing_note="Spun off from HON, began trading 29 Jun 2026"),
    "CBRS": TickerInfo(listing_date=date(2026, 5, 14), listing_kind="IPO",
                       listing_note="IPO 14 May 2026"),
    "FPS": TickerInfo(listing_date=date(2026, 2, 5), listing_kind="IPO",
                      listing_note="IPO 5 Feb 2026 - early-life, expect volatility"),
    "HON": TickerInfo(reverse_split=(date(2026, 6, 29), 2.0),
                      extra_note="1-for-2 reverse split 29 Jun 2026"),
    "SBGSY": TickerInfo(thin=True, extra_note="Thinly traded OTC ADR"),
}

# Display labels only, used when Yahoo does not return a name. Never a data point.
FALLBACK_NAMES: dict[str, str] = {
    "NVDA": "NVIDIA Corp", "MSFT": "Microsoft Corp", "MRNA": "Moderna Inc",
    "AMZN": "Amazon.com Inc", "AAPL": "Apple Inc", "RKLB": "Rocket Lab Corp",
    "GOOG": "Alphabet Inc Class C", "GLW": "Corning Inc",
    "MELI": "MercadoLibre Inc", "ASML": "ASML Holding NV",
    "VRTX": "Vertex Pharmaceuticals", "KARO": "Karooooo Ltd",
    "MOG.A": "Moog Inc Class A", "GOOGL": "Alphabet Inc Class A",
    "TMDX": "TransMedics Group", "KRYS": "Krystal Biotech Inc",
    "TSLA": "Tesla Inc", "MDT": "Medtronic plc", "NU": "Nu Holdings Ltd",
    "FPS": "Forgent Power Solutions Inc", "SHOP": "Shopify Inc",
    "OC": "Owens Corning", "TKO": "TKO Group Holdings", "MRVL": "Marvell Technology",
    "CDNS": "Cadence Design Systems", "PLD": "Prologis Inc",
    "META": "Meta Platforms Inc", "SYM": "Symbotic Inc", "AVGO": "Broadcom Inc",
    "SBGSY": "Schneider Electric SE (ADR)", "UTHR": "United Therapeutics",
    "IBM": "International Business Machines", "INTC": "Intel Corp",
    "MLI": "Mueller Industries Inc", "RDDT": "Reddit Inc", "KBR": "KBR Inc",
    "NBIS": "Nebius Group NV", "WRBY": "Warby Parker Inc",
    "CBRS": "Cerebras Systems Inc", "BWXT": "BWX Technologies Inc",
    "HON": "Honeywell International Inc", "HONA": "Honeywell Aerospace Inc",
}

log = logging.getLogger("portfolio")


# ---------------------------------------------------------------------------
# yfinance plumbing
# ---------------------------------------------------------------------------

class _YFLogCapture(logging.Handler):
    """Capture yfinance's own warnings.

    yfinance logs network/HTTP errors and returns an empty frame instead of
    raising, so without this the only available error is an unhelpful
    "no data". Capturing its log surfaces the real cause.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(" ".join(record.getMessage().split()))
        except Exception:                                   # noqa: BLE001
            pass

    def drain(self) -> str:
        seen, unique = set(), []
        for m in self.messages:
            if m and m not in seen:
                seen.add(m)
                unique.append(m)
        self.messages.clear()
        return " | ".join(unique)[:300]


_YF_LOG = _YFLogCapture()


def _init_yf():
    import yfinance as yf
    logger = logging.getLogger("yfinance")
    if _YF_LOG not in logger.handlers:
        logger.addHandler(_YF_LOG)
        logger.setLevel(logging.WARNING)
    return yf


def symbol_candidates(ticker: str) -> list[str]:
    """yfinance wants a hyphen where the exchange uses a dot (MOG.A -> MOG-A)."""
    out = [ticker]
    if "." in ticker:
        out.append(ticker.replace(".", "-"))
    return out


def _normalise_close(series) -> "pd.Series | None":
    """Coerce a raw close column into a clean, naive-indexed, positive series."""
    if series is None or len(series) == 0:
        return None
    if isinstance(series, pd.DataFrame):
        if series.shape[1] == 0:
            return None
        series = series.iloc[:, 0]
    idx = pd.DatetimeIndex(series.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    out = pd.Series(series.to_numpy(), index=idx.normalize(), name="Close")
    out = pd.to_numeric(out, errors="coerce").dropna()
    out = out[out > 0]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out if len(out) else None


def _close_from_frame(df, symbol: str | None = None) -> "pd.Series | None":
    """Extract one symbol's Close column from a single- or multi-ticker frame."""
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        for level in range(df.columns.nlevels):
            if "Close" not in df.columns.get_level_values(level):
                continue
            sub = df.xs("Close", axis=1, level=level)
            if isinstance(sub, pd.DataFrame):
                if symbol is not None:
                    # A symbol absent from a batch response must read as "no
                    # data". Falling through to the first column here would
                    # silently hand back a DIFFERENT ticker's prices.
                    if symbol not in sub.columns:
                        return None
                    sub = sub[symbol]
                elif sub.shape[1] == 1:
                    sub = sub.iloc[:, 0]
                else:
                    return None          # ambiguous: many tickers, none named
            return _normalise_close(sub)
        return None
    if "Close" in df.columns:
        return _normalise_close(df["Close"])
    return None


def batch_history(symbols: list[str], delay: float) -> dict[str, "pd.Series"]:
    """Fetch ~7 months of daily closes for every symbol in one request.

    One batched call instead of 42 keeps well clear of Yahoo's rate limits when
    the dashboard refreshes every 15-30 minutes. Symbols missing from the batch
    are retried individually by fetch_history_single().
    """
    yf = _init_yf()
    out: dict[str, pd.Series] = {}
    _YF_LOG.drain()
    try:
        df = yf.download(" ".join(symbols), period="7mo", interval="1d",
                         auto_adjust=True, progress=False, actions=False,
                         threads=False, group_by="column")
    except Exception as exc:                                # noqa: BLE001
        log.warning("batch history request failed (%s); falling back per ticker", exc)
        return out
    for sym in symbols:
        close = _close_from_frame(df, sym)
        if close is not None:
            out[sym] = close
    log.info("batch history: %d/%d symbols returned data", len(out), len(symbols))
    time.sleep(delay)
    return out


def fetch_history_single(symbol: str, delay: float) -> "pd.Series | None":
    """Per-symbol history fetch with one retry, used when the batch missed one."""
    yf = _init_yf()
    for attempt, kwargs in enumerate((
        {"period": "7mo"},
        {"start": (date.today() - timedelta(days=225)).isoformat(),
         "end": (date.today() + timedelta(days=1)).isoformat()},
    )):
        if attempt:
            time.sleep(delay)
        try:
            df = yf.download(symbol, interval="1d", auto_adjust=True,
                             progress=False, actions=False, threads=False, **kwargs)
        except Exception as exc:                            # noqa: BLE001
            log.warning("history %s attempt %d failed: %s", symbol, attempt + 1, exc)
            continue
        close = _close_from_frame(df)
        if close is not None:
            return close
    return None


def fetch_quote(symbol: str, delay: float) -> dict:
    """Live/most-recent price and previous close, with one retry.

    fast_info is the cheap endpoint; .info is not called here because 42 of them
    per refresh is exactly what gets a client throttled.
    """
    yf = _init_yf()
    for attempt in range(2):
        if attempt:
            time.sleep(delay * 2)                           # simple backoff
        try:
            fi = yf.Ticker(symbol).fast_info
            last = fi.get("last_price") if hasattr(fi, "get") else None
            prev = fi.get("previous_close") if hasattr(fi, "get") else None
            last = float(last) if last else None
            prev = float(prev) if prev else None
            if last and last > 0:
                return {"last": last, "prev_close": prev if prev and prev > 0 else None}
        except Exception as exc:                            # noqa: BLE001
            log.warning("quote %s attempt %d failed: %s", symbol, attempt + 1, exc)
    return {}


def fetch_name(symbol: str) -> str | None:
    """Company name, fetched only when not already cached in data.json."""
    yf = _init_yf()
    try:
        info = yf.Ticker(symbol).info or {}
        for key in ("longName", "shortName", "displayName"):
            if info.get(key):
                return str(info[key])
    except Exception as exc:                                # noqa: BLE001
        log.warning("name lookup %s failed: %s", symbol, exc)
    return None


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

def close_on_or_before(close: "pd.Series", target: date):
    """Close for `target`, or the nearest prior trading day.

    Returns None when the series starts after `target` -- never interpolated,
    never carried backwards across a gap.
    """
    window = close.loc[:pd.Timestamp(target)]
    if len(window) == 0:
        return None
    return float(window.iloc[-1]), window.index[-1].date()


def pct(current: float | None, past: float | None) -> float | None:
    if current is None or past is None or past == 0:
        return None
    return round((current - past) / past * 100.0, 2)


def longest_flat_run(close: "pd.Series") -> int:
    """Longest run of identical consecutive closes -- a stale-quote smell."""
    values, longest, run = close.to_numpy(), 1, 1
    for i in range(1, len(values)):
        run = run + 1 if values[i] == values[i - 1] else 1
        longest = max(longest, run)
    return longest if len(values) else 0


def split_artifact(close: "pd.Series", split_date: date, ratio: float) -> bool:
    """True if an unadjusted split jump is still visible around `split_date`.

    With auto_adjust=True the pre-split closes are already divided by the ratio,
    so no ~ratio-sized single-day gap should remain. One that does means the
    adjustment did not land and the historical prices are off by that factor.
    """
    window = close.loc[pd.Timestamp(split_date) - pd.Timedelta(days=7):
                       pd.Timestamp(split_date) + pd.Timedelta(days=7)]
    values = window.to_numpy()
    for i in range(1, len(values)):
        if values[i - 1] <= 0:
            continue
        move = values[i] / values[i - 1]
        if abs(move - ratio) < 0.15 or abs(move - 1.0 / ratio) < 0.15 / ratio:
            return True
    return False


def resolve_good_buys() -> dict[str, str]:
    """Map ticker -> "Tessa" | "Graeme" | "Both" from the GOOD_BUYS config.

    A ticker both people rate resolves to "Both" on its own; there is no
    separate "both" list to keep in sync.
    """
    counts: dict[str, list[str]] = {}
    for person, tickers in GOOD_BUYS.items():
        for t in tickers:
            counts.setdefault(t.upper(), []).append(person)
    return {t: (people[0] if len(people) == 1 else "Both")
            for t, people in counts.items()}


def build_position(ticker: str, owner_shares: dict[str, float], symbol: str,
                   close: "pd.Series", quote: dict, name: str | None) -> dict:
    """Assemble one dashboard row. Missing values are null, never estimated."""
    info = TICKER_INFO.get(ticker, TickerInfo())
    notes: list[str] = []

    last_close = float(close.iloc[-1])
    last_close_date = close.index[-1].date()

    # Prefer the live intraday print; fall back to the last daily close.
    if quote.get("last"):
        price = quote["last"]
        price_source = "intraday"
        price_as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        price = last_close
        price_source = "close"
        price_as_of = last_close_date.isoformat()
        notes.append("No live quote - showing last daily close")

    # 1-day change needs the close *before* the current price's own session.
    prev_close = quote.get("prev_close")
    if prev_close is None:
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else None
    chg_1d = pct(price, prev_close)

    row: dict = {
        "ticker": ticker,
        "symbol_used": symbol,
        "name": name or FALLBACK_NAMES.get(ticker) or ticker,
        # `shares` is the combined position; `shares_by_owner` is how it splits.
        # A jointly held ticker sums here and stays one row, so the combined
        # view counts it once at full size.
        "shares": round(sum(owner_shares.values()), 6),
        "shares_by_owner": dict(owner_shares),
        "price": round(price, 2),
        "price_source": price_source,
        "price_as_of": price_as_of,
        "prev_close": round(prev_close, 2) if prev_close else None,
        "chg_1d": chg_1d,
        "last_close_date": last_close_date.isoformat(),
        "stale": False,
    }

    if symbol != ticker:
        notes.append(f"Resolved as {symbol}")

    # --- look-back changes -------------------------------------------------
    first_date = close.index[0].date()
    missing: list[str] = []
    for label, days in PERIODS:
        found = close_on_or_before(close, last_close_date - timedelta(days=days))
        if found is None:
            row[f"chg_{label}"] = None
            row[f"price_{label}"] = None
            missing.append(label.upper())
            continue
        past, _ = found
        row[f"price_{label}"] = round(past, 2)
        row[f"chg_{label}"] = pct(price, past)

    # One note for all the missing look-backs, not one note each.
    if missing:
        if info.listing_date is not None:
            notes.append(f"No {'/'.join(missing)} data - pre-{info.listing_kind} "
                         f"({info.listing_date:%-d %b %Y})")
        else:
            notes.append(f"No {'/'.join(missing)} data - history starts {first_date}")

    # Short-history tickers get a since-listing change instead of a blank 6m.
    if info.listing_date is not None and row.get("chg_6m") is None:
        row["chg_since_ipo"] = pct(price, float(close.iloc[0]))
        row["since_ipo_from"] = first_date.isoformat()
    else:
        row["chg_since_ipo"] = None
        row["since_ipo_from"] = None

    # --- 6-month peak and drawdown ----------------------------------------
    window_start = pd.Timestamp(last_close_date - timedelta(days=SIX_MONTH_DAYS))
    window = close.loc[window_start:]
    if len(window) == 0:
        window = close
    peak = float(window.max())
    # The live price can exceed the historical peak intraday; a new high is a
    # 0% drawdown, never a positive one.
    effective_peak = max(peak, price)
    row["peak_6m"] = round(peak, 2)
    row["peak_6m_date"] = window.idxmax().date().isoformat()
    row["drawdown_pct"] = pct(price, effective_peak)
    if close.index[0] > window_start:
        notes.append(f"Peak covers partial history from {first_date}")

    # --- sanity checks ------------------------------------------------------
    if info.listing_note and not missing:
        notes.append(info.listing_note)
    if info.extra_note:
        notes.append(info.extra_note)
    if info.reverse_split and split_artifact(close, *info.reverse_split):
        ratio = info.reverse_split[1]
        notes.append(f"VERIFY: ~{ratio:g}x gap at the split date - "
                     f"history may be unadjusted")
    flat = longest_flat_run(close)
    if flat >= 3:
        notes.append(f"Stale quote? {flat} identical closes in a row")

    row["notes"] = notes
    return row


def carry_forward(previous: dict, reason: str,
                  owner_shares: dict[str, float] | None = None) -> dict:
    """Keep a failed ticker's last-known-good row rather than blanking it.

    Prices carry forward; share counts do not. If the config's split changed
    since the last good fetch, the new split wins -- a stale price against a
    current holding, never a stale holding.
    """
    row = dict(previous)
    if owner_shares is not None:
        row["shares"] = round(sum(owner_shares.values()), 6)
        row["shares_by_owner"] = dict(owner_shares)
    row["stale"] = True
    row["stale_reason"] = reason
    notes = [n for n in row.get("notes", []) if not n.startswith("Stale:")]
    notes.insert(0, f"Stale: {reason}")
    row["notes"] = notes
    return row


def fetch_fx(delay: float, previous: dict | None) -> dict:
    """USD -> NZD rate, with the quoting convention verified rather than assumed.

    Yahoo's "{CCY}=X" should mean CCY per 1 USD (~1.7 for NZD). If a value comes
    back inverted (~0.6) it is flipped and noted; an implausible value falls back
    to the last good rate.
    """
    quote = fetch_quote(FX_TICKER, delay)
    raw = quote.get("last")
    note = ""
    rate = None
    if raw:
        lo, hi = FX_PLAUSIBLE
        if lo <= raw <= hi:
            rate = raw
        elif 1 / hi <= raw <= 1 / lo:
            rate = 1.0 / raw
            note = f"Yahoo returned {raw:.4f} (USD per NZD); inverted to NZD per USD"
            log.warning("FX %s", note)
        else:
            log.warning("FX rate %.4f outside plausible range %s; ignoring", raw, FX_PLAUSIBLE)

    if rate is not None:
        return {"usd_nzd": round(rate, 4),
                "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "stale": False, "note": note}

    if previous and previous.get("usd_nzd"):
        log.warning("FX fetch failed; carrying forward %.4f", previous["usd_nzd"])
        return {**previous, "stale": True,
                "note": "FX fetch failed - showing last known rate"}
    log.error("FX fetch failed and no previous rate available")
    return {"usd_nzd": None, "as_of": None, "stale": True,
            "note": "FX unavailable - NZD values cannot be shown"}


# ---------------------------------------------------------------------------
# Refresh orchestration
# ---------------------------------------------------------------------------

def load_previous(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:                                # noqa: BLE001
        log.warning("could not read existing %s (%s); starting fresh", path, exc)
        return {}


def refresh(path: str, delay: float) -> dict:
    """One full refresh pass. Always returns a complete payload."""
    started = datetime.now(timezone.utc)
    previous = load_previous(path)
    prev_rows = {r["ticker"]: r for r in previous.get("positions", [])}
    good_buys = resolve_good_buys()

    # Resolve each holding to a working yfinance symbol, reusing whatever the
    # last run proved worked so dotted tickers are not re-probed every 15 min.
    wanted: dict[str, str] = {}
    for ticker in HOLDINGS:
        cached = prev_rows.get(ticker, {}).get("symbol_used")
        wanted[ticker] = cached if cached else symbol_candidates(ticker)[0]

    history = batch_history(sorted(set(wanted.values())), delay)

    positions: list[dict] = []
    failures: list[str] = []

    for ticker, owner_shares in HOLDINGS.items():
        close = history.get(wanted[ticker])
        symbol = wanted[ticker]

        # Not in the batch: retry each candidate spelling individually.
        if close is None:
            for candidate in symbol_candidates(ticker):
                close = fetch_history_single(candidate, delay)
                if close is not None:
                    symbol = candidate
                    break
                time.sleep(delay)

        if close is None:
            reason = _YF_LOG.drain() or "no price history returned"
            log.error("%s: %s", ticker, reason)
            failures.append(ticker)
            if ticker in prev_rows:
                positions.append(carry_forward(prev_rows[ticker], reason, owner_shares))
            else:
                positions.append({
                    "ticker": ticker, "symbol_used": symbol,
                    "name": FALLBACK_NAMES.get(ticker, ticker),
                    "shares": round(sum(owner_shares.values()), 6),
                    "shares_by_owner": dict(owner_shares),
                    "price": None, "stale": True, "stale_reason": reason,
                    "good_buy": good_buys.get(ticker),
                    "notes": [f"Never fetched successfully: {reason}"],
                })
            continue

        quote = fetch_quote(symbol, delay)
        name = prev_rows.get(ticker, {}).get("name")
        if not name or name == ticker:
            name = fetch_name(symbol) or FALLBACK_NAMES.get(ticker)

        try:
            row = build_position(ticker, owner_shares, symbol, close, quote, name)
        except Exception as exc:                            # noqa: BLE001
            log.exception("%s: row build failed", ticker)
            failures.append(ticker)
            positions.append(carry_forward(prev_rows.get(ticker, {
                "ticker": ticker, "name": FALLBACK_NAMES.get(ticker, ticker),
                "shares": round(sum(owner_shares.values()), 6),
                "shares_by_owner": dict(owner_shares), "notes": []}),
                f"calculation error: {exc}"))
            continue

        row["good_buy"] = good_buys.get(ticker)
        positions.append(row)
        time.sleep(delay)

    fx = fetch_fx(delay, previous.get("fx"))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        "fx": fx,
        "owners": OWNERS,
        "default_view": DEFAULT_VIEW,
        "split_supplied": split_is_supplied(),
        "unattributed_label": UNATTRIBUTED,
        "good_buys": GOOD_BUYS,
        "failures": failures,
        "positions": positions,
    }

    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)          # atomic, so the page never reads a half file

    priced = sum(1 for p in positions if p.get("price") and not p.get("stale"))
    log.info("wrote %s: %d/%d priced, %d stale, fx=%s%s",
             path, priced, len(positions), len(positions) - priced,
             fx.get("usd_nzd"), " (stale)" if fx.get("stale") else "")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=OUT_PATH, help=f"output JSON (default: {OUT_PATH})")
    parser.add_argument("--delay", type=float, default=0.4,
                        help="seconds between requests (default: 0.4)")
    parser.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                        help="refresh forever on this interval instead of once")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr)

    if not args.watch:
        refresh(args.out, args.delay)
        return 0

    log.info("watch mode: refreshing every %d seconds (Ctrl-C to stop)", args.watch)
    while True:
        try:
            refresh(args.out, args.delay)
        except KeyboardInterrupt:
            log.info("stopped")
            return 0
        except Exception:                                   # noqa: BLE001
            # A refresh must never kill the loop; the page keeps serving the
            # previous data.json until the next pass succeeds.
            log.exception("refresh failed; retrying next interval")
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
