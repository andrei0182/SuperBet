#!/bin/bash
# init_project.sh — scaffolds the SuperBet.ro scraper project from scratch.
# This is Phase 0: project structure + a reconnaissance tool, NOT a working
# scraper yet — we don't know Superbet.ro's actual data structure (server
# rendered vs JS-rendered, any underlying AJAX/JSON endpoints) until we
# inspect it live, same methodology that worked for the BetExplorer
# project today. Run this once, inside a fresh Codespace on the SuperBet
# repo, then follow the printed next steps.
set -e

mkdir -p superbet_scraper tools output

cat > requirements.txt << 'EOF'
selenium>=4.20
webdriver-manager>=4.0
requests>=2.31
pandas>=2.0
openpyxl>=3.1
EOF

cat > .gitignore << 'EOF'
__pycache__/
*.pyc
output/*.xlsx
.venv/

# Session scratch/debug files — never commit these
test_*.py
debug_*.py
*.html
chromedriver.log
run_log*.txt
run_final*.txt
EOF

cat > superbet_scraper/__init__.py << 'EOF'
EOF

cat > superbet_scraper/driver.py << 'EOF'
from __future__ import annotations

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


def build_driver(headless: bool = True, window_size: str = "1920,1080") -> webdriver.Chrome:
    """Headless Chrome, same pattern as the BetExplorer project.

    STATUS: UNCONFIRMED whether Superbet.ro actually needs a real browser at
    all — the initial recon in tools/inspect_page.py exists to answer that.
    If plain `requests` turns out to work (as it did for BetExplorer's odds
    and stats, once the right endpoints were found), prefer that — it's far
    faster and more reliable, per today's experience. Keep this around only
    for whatever step (if any) genuinely needs JS execution.
    """
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument(f"--window-size={window_size}")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)
EOF

cat > superbet_scraper/models.py << 'EOF'
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Odds1X2:
    home: Optional[float] = None
    draw: Optional[float] = None
    away: Optional[float] = None


@dataclass
class OddsOverUnder:
    line: float = 2.5
    over: Optional[float] = None
    under: Optional[float] = None


@dataclass
class Match:
    """PLACEHOLDER shape — refine once Superbet.ro's real data structure is
    confirmed (see tools/inspect_page.py). Fields below mirror the
    BetExplorer project's Match model as a reasonable starting guess, not a
    confirmed spec.
    """
    league: str
    home_team: str
    away_team: str
    time_text: str
    status: str  # "scheduled" | "live" | "completed" — confirm actual values used by Superbet.ro
    odds_1x2: Odds1X2 = field(default_factory=Odds1X2)
    odds_ou: OddsOverUnder = field(default_factory=OddsOverUnder)
    match_url: Optional[str] = None

    def to_flat_dict(self) -> dict:
        return {
            "league": self.league,
            "time": self.time_text,
            "status": self.status,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "odds_1": self.odds_1x2.home,
            "odds_x": self.odds_1x2.draw,
            "odds_2": self.odds_1x2.away,
            "ou_line": self.odds_ou.line,
            "odds_over": self.odds_ou.over,
            "odds_under": self.odds_ou.under,
            "match_url": self.match_url,
        }
EOF

cat > tools/inspect_page.py << 'EOF'
"""Reconnaissance tool for Superbet.ro — run this FIRST, before writing any
scraping logic. Mirrors the tool that was essential for the BetExplorer
project: figure out what's actually in the page before guessing selectors.

Usage:
    python tools/inspect_page.py "https://superbet.ro/pariuri-sportive/fotbal/italia/serie-a/toate"

What to look for in the output:
1. Does plain `requests` (no browser) already see match/odds text? If yes,
   the whole project can likely skip Selenium entirely (as ended up being
   true for BetExplorer's odds + stats, once the AJAX endpoints were found).
2. If not, what does Selenium see that requests doesn't? That gap is what
   needs JS execution.
3. Any XHR/fetch calls visible in browser dev tools (check manually,
   outside this script, via the Network tab) pointing to a JSON API —
   that would let us skip both DOM scraping approaches entirely, which is
   the fastest and most reliable option when it exists (confirmed pattern
   from BetExplorer's match-odds AJAX endpoint).
"""
from __future__ import annotations

import argparse
import sys

import requests


def check_via_requests(url: str) -> None:
    print(f"\n=== Plain requests check: {url} ===")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        return

    text = resp.text
    print(f"Status: {resp.status_code}, length: {len(text)} chars")

    # Heuristic: does the raw HTML contain what looks like real match/odds
    # content, or just an empty app shell waiting for JS?
    markers = ["câştigă", "Egalitate", "cote", "vs", "FinalNapoli", "FinalTorino"]
    found = [m for m in markers if m in text]
    print(f"Content markers found: {found if found else 'NONE — likely JS-rendered, needs a browser'}")

    with open("/tmp/superbet_requests_check.html", "w", encoding="utf-8") as f:
        f.write(text)
    print("Saved full response to /tmp/superbet_requests_check.html for manual inspection.")


def check_via_selenium(url: str) -> None:
    print(f"\n=== Selenium (real browser) check: {url} ===")
    try:
        from superbet_scraper.driver import build_driver
    except ImportError:
        print("Could not import build_driver — run this from the project root.")
        return

    driver = build_driver(headless=True)
    try:
        driver.get(url)
        import time
        time.sleep(5)  # let JS settle
        html = driver.page_source
        print(f"Selenium page_source length: {len(html)} chars")
        with open("/tmp/superbet_selenium_check.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Saved full rendered HTML to /tmp/superbet_selenium_check.html for manual inspection.")
    finally:
        driver.quit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a Superbet.ro page: requests vs Selenium.")
    parser.add_argument("url", help="Superbet.ro URL to inspect")
    parser.add_argument("--skip-selenium", action="store_true", help="Only run the requests check")
    args = parser.parse_args()

    check_via_requests(args.url)
    if not args.skip_selenium:
        check_via_selenium(args.url)


if __name__ == "__main__":
    main()
EOF

cat > README.md << 'EOF'
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
EOF

echo "Project scaffolded."
echo ""
echo "Next steps:"
echo "  1. git add -A && git commit -m 'Initial project scaffold + recon tool' && git push"
echo "  2. Open a Codespace on the SuperBet repo"
echo "  3. pip install -r requirements.txt"
echo "  4. python tools/inspect_page.py \"https://superbet.ro/pariuri-sportive/fotbal/italia/serie-a/toate\""
echo "  5. Send me the output — we'll design the real scraper from there."
