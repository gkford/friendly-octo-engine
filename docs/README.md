# Portfolio dashboard

A live-updating view of the 42-position portfolio: current prices, look-back
changes, drawdown from the trailing 6-month peak, and holding values in USD and
NZD.

- `index.html` — the dashboard. Plain HTML/CSS/JS, no build step.
- `data.json` — the data it renders, rewritten by `scripts/fetch_prices.py`.

The page re-reads `data.json` every 60 seconds, so it updates without a manual
reload. The data behind it is refreshed by the scheduled job described below.

## How it fits together

```
.github/workflows/portfolio-refresh.yml   cron, every 20 min in US market hours
            │  runs
            ▼
scripts/fetch_prices.py    yfinance ──► Yahoo Finance
            │  writes
            ▼
docs/data.json  ◄────── re-read every 60s ────── docs/index.html (your browser)
```

## Deploying it

GitHub Pages serves this folder directly — no build, no third-party host.

1. **Merge this branch into the default branch.** This is not optional:
   GitHub only runs `schedule` triggers on the default branch, so the refresh
   job does nothing until the workflow file lives there.
2. **Settings → Pages → Source: Deploy from a branch**, branch `main`,
   folder `/docs`. The dashboard is then at
   `https://<user>.github.io/<repo>/`.
3. **Actions → Refresh portfolio prices → Run workflow** to populate
   `data.json` immediately rather than waiting for the next scheduled slot.

`data.json` ships as a placeholder with every holding listed and every price
`null`, so the page renders a complete table reading "Awaiting first refresh"
before that first run. No placeholder price is ever invented.

If the repository is private, Pages needs a paid plan. The alternatives are to
make the repo public, or to serve the folder yourself:

```bash
python3 -m http.server 8000 --directory docs     # then open localhost:8000
```

## Running the refresh yourself

```bash
pip install yfinance
python3 scripts/fetch_prices.py            # one refresh
python3 scripts/fetch_prices.py --watch 900   # re-run every 15 min, for a Pi or a VPS
```

`--watch` is the option to use if you would rather run this on a machine you
own than on GitHub's schedule. A failed pass is logged and the loop continues;
the page keeps serving the previous `data.json` until one succeeds.

## Changing the schedule

Edit the `cron` line in `.github/workflows/portfolio-refresh.yml`. It is UTC,
and `*/20 13-21 * * 1-5` covers 09:30–16:00 New York under both EST and EDT.
Tightening it to `*/15` is fine; each run takes about a minute, and Actions
minutes are free on public repositories.

## Whose portfolio: the view toggle

The toggle above the table switches between **Graeme's**, **Tessa's** and
**Combined**. Graeme's is the default (`DEFAULT_VIEW` in
`scripts/fetch_prices.py`); the last choice made in a browser is remembered
there.

Everything downstream follows the toggle: which rows appear, the shares and
value columns, the totals row, and the stat cards.

### How combining works

Shares are stored per owner, one entry per person, on a single row per ticker:

```python
HOLDINGS = {
    "AMZN": {"Graeme": 14.9084},                    # Graeme only
    "GLW":  {"Tessa": 13.7832},                     # Tessa only
    "NVDA": {"Graeme": 20.0, "Tessa": 11.2714},     # both
}
```

A ticker you both hold stays **one row**. The Combined view sums the entries,
so that position is counted once at its full size — it is not dropped, and it
does not appear twice. Combined total always equals Graeme's total plus
Tessa's, which the test suite asserts directly. In the Combined view, a jointly
held row shows its split under the share count (`G 20 · T 11.2714`) so the
arithmetic is checkable at a glance.

### The split has not been supplied yet

`HOLDINGS` currently attributes every position to `UNATTRIBUTED`, because the
original 42-line brief was already a merge of the two sources with no record of
which line came from where. While that is true:

- `split_is_supplied()` returns `False`
- the per-owner toggle buttons are **disabled**
- the page shows Combined only, with a banner explaining why

That is deliberate. Guessing the split would produce a per-owner total that
silently omits or duplicates somebody's shares, which is worse than not
offering the view. Fill in the real per-owner numbers and the toggle enables
itself — no other change needed.

## Editing the good-buy ratings

These tags record **who rates a holding as a good buy**. They say nothing about
who owns it or who is watching it — every position in the table is held.

`GOOD_BUYS` at the top of `scripts/fetch_prices.py`:

```python
GOOD_BUYS = {
    "Tessa":  ["AMZN", "GLW", "MRVL", "INTC", "KBR", "HON", "RKLB"],
    "Graeme": ["RKLB", "NBIS"],
}
```

A ticker in more than one list renders as **Both** automatically — that is how
RKLB gets its tag; there is no separate "both" list to maintain. Adding a
*third person* also needs one more colour in `index.html`: see the comment above
the `--cat-*` tokens, which explains why those three hues were chosen.

## What the colours mean

- Row shading and the ticker chip: who rates that holding a good buy
  (Tessa / Graeme / Both). Every shaded row also carries a text chip, so the
  colour is never the only signal.
- Green and red: gains and losses, on every percentage and on the drawdown bar.
  Each carries a ▲/▼ as well, for the same reason.
- An amber `!` beside a ticker means that row has a flag in its Notes column —
  a stale carry-forward, a missing look-back, or a corporate action to verify.

The three rating hues were validated for colour-blind separation and contrast
in both light and dark themes; substituting them casually will break that.

## Reliability behaviour

| Situation | What happens |
|---|---|
| One ticker fails to fetch | Keeps its last-known-good values, marked **Stale** with the reason; the rest of the refresh proceeds |
| FX fetch fails | Last known rate is carried forward and flagged stale in the header and totals |
| A look-back predates a listing (CBRS, HONA) | Cell reads **N/A**, with a "since listing" change shown underneath where one exists |
| Yahoo returns a split-unadjusted history | Row is flagged `VERIFY: ~2x gap at the split date` |
| A thin ADR stops printing new quotes | Row is flagged `Stale quote? N identical closes in a row` |
| `data.json` cannot be loaded at all | The page keeps the last data it loaded and shows a banner |

Nothing is interpolated, estimated, or carried across a gap in history. A value
that cannot be derived reads N/A.
