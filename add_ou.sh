#!/bin/bash
# add_ou.sh — adds Over/Under odds extraction via the per-event detail
# endpoint (confirmed structure: market "Total goluri", pairs of
# "Sub {line}"/"Peste {line}" sharing a marketUuid). This endpoint is heavy
# (~3.8MB per match, 300+ markets) — fetch it only for matches you actually
# need O/U for, not for every match in a listing (use the lightweight
# events.py list endpoint for that).
# Run from the repo root: bash add_ou.sh
set -e

python3 << 'PYEOF_ANDREI'
path = "superbet_scraper/events.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_import = "from .models import Match, Odds1X2"
new_import = "from .models import Match, Odds1X2, OddsOverUnder"
if old_import not in content:
    raise SystemExit("ERROR: models import not found verbatim — aborting without changes.")
content = content.replace(old_import, new_import, 1)

addition = '''

EVENT_DETAIL_URL = "https://production-superbet-offer-ro.freetls.fastly.net/v2/ro-RO/events/{event_id}"


def fetch_event_detail(event_id: int, timeout: float = 15.0) -> dict | None:
    """Fetch the FULL per-match odds detail (all ~300 markets, ~3-4MB per
    match). CONFIRMED (2026-09-14) via curl: plain GET, no session needed.

    This is heavy — only call it for matches you actually need beyond the
    lightweight fetch_events() list (e.g. for Over/Under odds, which aren't
    included in that lighter endpoint's default "Final" market only).
    Returns None on any failure.
    """
    url = EVENT_DETAIL_URL.format(event_id=event_id)
    try:
        resp = _session.get(url, timeout=timeout)
        resp.raise_for_status()
        result = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("fetch_event_detail: request failed for event_id=%s: %s", event_id, exc)
        return None

    if result.get("error") or not result.get("data"):
        return None
    return result["data"][0]


def parse_over_under(event_detail: dict, line: float = 2.5) -> OddsOverUnder:
    """Extract Over/Under odds for one line from an event_detail dict (see
    fetch_event_detail). CONFIRMED (2026-09-14) structure: market name
    "Total goluri", with paired entries sharing a marketUuid — one with
    name "Sub {line}" (Under) and one "Peste {line}" (Over), e.g. "Sub 2.5"
    / "Peste 2.5" for the 2.5 line.
    """
    under_name = f"Sub {line:g}"
    over_name = f"Peste {line:g}"
    result = OddsOverUnder(line=line)

    for odd in event_detail.get("odds", []):
        if odd.get("marketName") != "Total goluri":
            continue
        name = odd.get("name")
        if name == over_name:
            result.over = odd.get("price")
        elif name == under_name:
            result.under = odd.get("price")

    return result'''

content = content.rstrip("\n") + addition + "\n"

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched superbet_scraper/events.py: added fetch_event_detail() and parse_over_under().")
PYEOF_ANDREI

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py tools/*.py && echo "OK — syntax valid."
