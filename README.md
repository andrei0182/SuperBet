# SuperBet.ro scraper

**Status: Phase 0 — reconnaissance scaffold, not a working scraper yet.**

Goal (mirrors the BetExplorer project): scrape odds (1X2, Over/Under) and
team history/stats from superbet.ro, exported to a formatted Excel file.

## Why this starts as a scaffold, not finished code

We don't yet know Superbet.ro's actual data structure — whether pages are
server-rendered (plain `requests` sees everything, like BetExplorer's
league pages and AJAX odds endpoints turned out to be) or need real JS
execution (like BetExplorer's per-match standings widget, which was
unreliable via Selenium and only worked reliably once we found the
equivalent AJAX endpoint instead). Guessing wrong here wastes a lot of time
— today's BetExplorer project spent real effort on exactly this mistake
more than once. So: investigate first, then build.

## Setup

```bash
pip install -r requirements.txt
```

## Step 1 — investigate (do this first)

```bash
python tools/inspect_page.py "https://superbet.ro/pariuri-sportive/fotbal/italia/serie-a/toate"
```

This checks the same URL two ways — plain HTTP request, and a real
(headless) browser — and tells you whether match/odds content is present
in the raw HTML or only appears after JS runs. It also saves both versions
to `/tmp/` for manual inspection (`grep`, or download and open in a
browser).

**Also check manually, in your own browser's DevTools Network tab**, while
browsing a Superbet.ro odds page: filter for `Fetch/XHR` requests. If
there's a JSON API behind the page (very likely for a modern betting site),
that's usually the fastest and most reliable data source — skip both DOM
scraping approaches entirely and hit that endpoint directly with
`requests`, the same pattern that ended up working best for BetExplorer's
odds and team-stats data today.

## Once the data source is confirmed

Update this README and `superbet_scraper/models.py`'s `Match` fields to
match reality, then build out the actual scraping logic — `driver.py` is
already in place if Selenium turns out to be needed for any step, and
`requirements.txt` already includes `requests` for the AJAX/JSON path if
that's what we find instead.

## Project layout

- `superbet_scraper/driver.py` — headless Chrome builder (only needed if a
  step genuinely requires JS execution — confirm this before relying on it).
- `superbet_scraper/models.py` — data model, currently a placeholder mirroring
  the BetExplorer project's shape.
- `tools/inspect_page.py` — **run this first**, on any Superbet.ro page you
  want to scrape, before writing extraction logic for it.
- `output/` — where exported Excel files will go (gitignored).
