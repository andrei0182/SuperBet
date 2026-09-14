#!/bin/bash
# build_scraper.sh — first real implementation: tournament name->ID mapping
# (from the confirmed static JSON) + fetching events/odds for a tournament
# via the confirmed AJAX endpoint. Pure `requests`, no Selenium needed for
# either piece — same lesson as the BetExplorer project.
# Run from the repo root: bash build_scraper.sh
set -e

cat > superbet_scraper/tournaments.py << 'EOF'
from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)

TOURNAMENT_MAP_URL = "https://superbet.ro/static/offerMappings/sportTournamentMap_ro-RO.json"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_session = requests.Session()
_session.headers.update({"User-Agent": _USER_AGENT})

_cached_map: dict[str, int] | None = None


def fetch_tournament_map(timeout: float = 15.0) -> dict[str, int]:
    """Fetch (and cache for this process) the full slug -> tournament_id
    mapping. CONFIRMED (2026-09-14) via curl: static JSON, no auth/session
    needed. Slug format: "{sport}---{country}---{league}", e.g.
    "fotbal---italia---serie-a" -> 104. 12,696 total entries across all
    sports as of this writing; ~2,837 are football ("fotbal---...").
    """
    global _cached_map
    if _cached_map is not None:
        return _cached_map

    resp = _session.get(TOURNAMENT_MAP_URL, timeout=timeout)
    resp.raise_for_status()
    _cached_map = resp.json()
    return _cached_map


def football_tournaments() -> dict[str, int]:
    """Just the football ("fotbal---...") entries from the full map."""
    return {k: v for k, v in fetch_tournament_map().items() if k.startswith("fotbal---")}


def find_tournament(country_slug: str, league_slug: str) -> int | None:
    """Look up one tournament's id by its country and league slug, e.g.
    find_tournament("italia", "serie-a") -> 104. Returns None if not found.
    """
    key = f"fotbal---{country_slug}---{league_slug}"
    return fetch_tournament_map().get(key)


def search_tournaments(query: str) -> dict[str, int]:
    """Case-insensitive substring search over football tournament slugs —
    useful for finding the right slug when you don't know it exactly, e.g.
    search_tournaments("italia") to see every Italian football competition.
    """
    query_lower = query.lower()
    return {k: v for k, v in football_tournaments().items() if query_lower in k.lower()}
EOF

cat > superbet_scraper/events.py << 'EOF'
from __future__ import annotations

import datetime as dt
import logging

import requests

from .models import Match, Odds1X2

logger = logging.getLogger(__name__)

EVENTS_URL = "https://production-superbet-offer-ro.freetls.fastly.net/v3/ro-RO/events"
FOOTBALL_SPORT_ID = 5  # CONFIRMED (2026-09-14) via curl — sports=5 in the events query
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_session = requests.Session()
_session.headers.update({"User-Agent": _USER_AGENT})


def _iso_utc(d: dt.date, end_of_day: bool = False) -> str:
    t = "23:59:59.999" if end_of_day else "00:00:00.000"
    return f"{d.isoformat()}T{t}Z"


def fetch_events(
    tournament_ids: list[int],
    date: dt.date,
    index: str = "active-prematch",
    timeout: float = 15.0,
) -> list[dict]:
    """Fetch raw event dicts for the given tournament(s) on a given date.

    CONFIRMED (2026-09-14) via curl: this endpoint needs no session/cookies,
    a plain GET works standalone. `index` is "active-prematch" for
    not-yet-started matches or "active-live" for in-progress ones (seen both
    in DevTools traffic) — CONFIRM whether a third value exists for
    already-finished matches, or whether completed-match odds simply aren't
    available here (would need Scorealarm or another endpoint instead).

    Returns the raw `events` list from the JSON response, unparsed — see
    parse_event() to convert one entry into a Match.
    """
    params = {
        "startDate": _iso_utc(date),
        "endDate": _iso_utc(date, end_of_day=True),
        "index": index,
        "sports": FOOTBALL_SPORT_ID,
        "tournaments": ",".join(str(t) for t in tournament_ids),
    }
    try:
        resp = _session.get(EVENTS_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        result = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("fetch_events: request failed for tournaments=%s date=%s: %s", tournament_ids, date, exc)
        return []

    return result.get("events", [])


def parse_event(event: dict) -> Match:
    """Convert one raw event dict (from fetch_events) into a Match.

    CONFIRMED (2026-09-14) structure: event["fixture"]["event_name"] is
    "Home·Away" (separated by "·", not " - "); event["markets"] is a list of
    betting markets, where the market named "Final" (id 547 seen so far —
    CONFIRM this id is stable/global vs per-tournament) holds the 1X2 prices
    under odds[].metadata.name ("1"/"X"/"2") and odds[].price.
    """
    fixture = event.get("fixture", {})
    event_name = fixture.get("event_name", "")
    if "·" in event_name:
        home_team, away_team = event_name.split("·", 1)
    else:
        home_team, away_team = event_name, ""

    odds_1x2 = Odds1X2()
    for market in event.get("markets", []):
        if market.get("name") != "Final":
            continue
        for odd in market.get("odds", []):
            name = odd.get("metadata", {}).get("name")
            price = odd.get("price")
            if name == "1":
                odds_1x2.home = price
            elif name == "X":
                odds_1x2.draw = price
            elif name == "2":
                odds_1x2.away = price

    return Match(
        league="",  # filled in by the caller, which knows which tournament this came from
        home_team=home_team.strip(),
        away_team=away_team.strip(),
        time_text=fixture.get("event_date", ""),
        status=event.get("inplay_stats_metadata", {}).get("status", ""),
        odds_1x2=odds_1x2,
        match_url=f"https://superbet.ro/cote/fotbal/{event.get('event_id')}" if event.get("event_id") else None,
    )
EOF

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py tools/*.py && echo "OK — syntax valid."
