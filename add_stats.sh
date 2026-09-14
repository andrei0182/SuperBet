#!/bin/bash
# add_stats.sh — adds superbet_scraper/stats.py: fetches and parses
# standings and head-to-head history via the confirmed Scorealarm
# endpoints. Both need IDs (table_id, team1_id/team2_id) that come from
# the fixture overview endpoint (see fetch_fixture_overview) — the full
# chain for one match is: event_id -> fixture overview -> table_id +
# team ids -> standings + h2h.
# Run from the repo root: bash add_stats.sh
set -e

cat > superbet_scraper/stats.py << 'EOF'
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_session = requests.Session()
_session.headers.update({"User-Agent": _USER_AGENT})

FIXTURE_OVERVIEW_URL = "https://scorealarm-stats.freetls.fastly.net/v2/soccer/fixtures/overview/rosuperbetsport/ro-RO"
STANDINGS_URL = "https://scorealarm-stats.freetls.fastly.net/v2/soccer/competitions/standings/table/rosuperbetsport/ro-RO"
H2H_URL = "https://scorealarm-stats.freetls.fastly.net/v2/soccer/fixtures/h2h/rosuperbetsport/ro-RO"


def fetch_fixture_overview(event_id: int, timeout: float = 15.0) -> dict | None:
    """Fetch the Scorealarm fixture overview for one match — the entry
    point for everything else in this module, since it's where table_id
    (for standings) and team1.id/team2.id (for h2h) come from.

    CONFIRMED (2026-09-14) via curl: fixture-id is "ax:match:{event_id}"
    using the same event_id as the Superbet offer API (superbet_scraper.events)
    — no separate id mapping needed. Also includes prematch_stats: each
    team's season averages (goals scored/conceded, shots, xG, cards,
    corners per game) — useful on its own, independent of standings/h2h.
    """
    params = {"fixture-id": f"ax:match:{event_id}"}
    try:
        resp = _session.get(FIXTURE_OVERVIEW_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("fetch_fixture_overview: request failed for event_id=%s: %s", event_id, exc)
        return None


def extract_table_id(fixture_overview: dict) -> str | None:
    return fixture_overview.get("table_id", {}).get("value")


def extract_team_ids(fixture_overview: dict) -> tuple[str | None, str | None]:
    team1_id = fixture_overview.get("team1", {}).get("id")
    team2_id = fixture_overview.get("team2", {}).get("id")
    return team1_id, team2_id


def fetch_standings(table_id: str, timeout: float = 15.0) -> list[dict]:
    """Fetch the full league standings table. Returns a simplified list of
    rows: [{"rank", "team_name", "team_id", ...raw column "data"}, ...].

    CONFIRMED (2026-09-14) structure: response has "standings_groups" (a
    league may have multiple, e.g. home/away splits — CONFIRM which group
    is the main/overall one; the first seen had "rows_away" specifically,
    suggesting there may be a parallel "rows_home"/"rows" — inspect a full
    response before assuming this covers everything). This function
    currently returns the raw rows from the first group's "rows_away"
    list — REFINE once the full group structure is confirmed.
    """
    params = {"table-id": table_id}
    try:
        resp = _session.get(STANDINGS_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("fetch_standings: request failed for table_id=%s: %s", table_id, exc)
        return []

    groups = data.get("standings_groups", [])
    if not groups:
        return []
    # TODO: confirm the right row list — "rows_away" was seen in initial
    # testing but a "rows" or "rows_home" key likely also exists.
    rows = groups[0].get("rows_away") or groups[0].get("rows") or []
    return [
        {
            "rank": row.get("rank"),
            "team_name": row.get("competitor_name"),
            "team_id": row.get("competitor_id"),
            "country_code": row.get("country_code"),
        }
        for row in rows
    ]


def fetch_h2h(team1_id: str, team2_id: str, timeout: float = 15.0) -> dict | None:
    """Fetch head-to-head history between two teams.

    CONFIRMED (2026-09-14) structure: "h2h_statistics" has aggregate
    win/draw/win counts (keys "team1"/"draw"/"team2") since "h2h_year_since";
    "h2h_events" is the full list of individual past matches with scores.
    """
    params = {"team1-id": team1_id, "team2-id": team2_id}
    try:
        resp = _session.get(H2H_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("fetch_h2h: request failed for %s vs %s: %s", team1_id, team2_id, exc)
        return None
EOF

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py tools/*.py && echo "OK — syntax valid."
