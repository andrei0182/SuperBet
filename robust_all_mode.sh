#!/bin/bash
# robust_all_mode.sh — replaces the batched-many-tournaments-per-request
# approach (unreliable — the same batch size succeeded once then failed
# repeatedly on retest, consistent with rate-limiting rather than a hard
# count/length limit) with the same architecture that worked for the
# BetExplorer project: ONE tournament id per request (confirmed reliable
# in isolation), run concurrently via a thread pool, with retry-with-backoff
# on 429/400/5xx so transient failures don't kill the whole run.
# Run from the repo root: bash robust_all_mode.sh
set -e

python3 << 'PYEOF_ANDREI'
path = "superbet_scraper/events.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_import = '''from __future__ import annotations

import datetime as dt
import logging

import requests

from .models import Match, Odds1X2, OddsOverUnder

logger = logging.getLogger(__name__)

EVENTS_URL = "https://production-superbet-offer-ro.freetls.fastly.net/v3/ro-RO/events"
FOOTBALL_SPORT_ID = 5  # CONFIRMED (2026-09-14) via curl — sports=5 in the events query
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_session = requests.Session()
_session.headers.update({"User-Agent": _USER_AGENT})'''

new_import = '''from __future__ import annotations

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Match, Odds1X2, OddsOverUnder

logger = logging.getLogger(__name__)

EVENTS_URL = "https://production-superbet-offer-ro.freetls.fastly.net/v3/ro-RO/events"
FOOTBALL_SPORT_ID = 5  # CONFIRMED (2026-09-14) via curl — sports=5 in the events query
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_session = requests.Session()
_session.headers.update({"User-Agent": _USER_AGENT})
_retry = Retry(total=5, backoff_factor=1.5, status_forcelist=[400, 429, 500, 502, 503, 504], respect_retry_after_header=True)
_session.mount("https://", HTTPAdapter(max_retries=_retry))
_session.mount("http://", HTTPAdapter(max_retries=_retry))'''

if old_import not in content:
    raise SystemExit("ERROR: events.py header block not found verbatim — aborting without changes.")
content = content.replace(old_import, new_import, 1)

addition = '''

def fetch_events_for_all_tournaments(
    tournament_ids: list[int],
    date: dt.date,
    index: str = "active-prematch",
    workers: int = 8,
    timeout: float = 15.0,
) -> list[dict]:
    """Fetch events across MANY tournaments by calling fetch_events once
    PER tournament id, concurrently via a thread pool — NOT by stuffing
    many ids into a single request's `tournaments=` param.

    CONFIRMED (2026-09-14): the events endpoint is unreliable with many
    tournament ids batched into one request (~30+ ids per request started
    failing with 400 Bad Request on retest, inconsistent with the URL
    length involved — behaves like rate-limiting rather than a hard
    per-request limit). A single tournament id per request has been
    reliable throughout testing, so this fetches one at a time, in
    parallel, with the module-level Retry adapter handling transient
    429/400/5xx automatically.
    """
    all_events: list[dict] = []

    def _fetch_one(tid: int) -> list[dict]:
        return fetch_events([tid], date, index=index, timeout=timeout)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, tid): tid for tid in tournament_ids}
        for future in as_completed(futures):
            tid = futures[future]
            try:
                events = future.result()
                all_events.extend(events)
            except Exception:
                logger.exception("fetch_events_for_all_tournaments: failed for tournament_id=%s", tid)

    return all_events
'''
content = content.rstrip("\n") + addition + "\n"

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched superbet_scraper/events.py: added fetch_events_for_all_tournaments() (one id per request, threaded, with retry).")
PYEOF_ANDREI

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py main.py tools/*.py && echo "OK — syntax valid."
