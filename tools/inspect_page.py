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
import os
import sys

# Ensure the project root (parent of this tools/ directory) is importable,
# regardless of how this script is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
