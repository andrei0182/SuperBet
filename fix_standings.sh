#!/bin/bash
# fix_standings.sh — replaces fetch_standings' placeholder row extraction
# with full parsing: uses "rows_total" (the overall/general standings — 20
# rows, confirmed complete) instead of the incomplete "rows_away", and
# extracts played/goal_difference/points, wins/draws/losses, and form from
# each row's structured "data" groups (matched against "headers_total").
# Run from the repo root: bash fix_standings.sh
set -e

python3 << 'PYEOF_ANDREI'
path = "superbet_scraper/stats.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '''def fetch_standings(table_id: str, timeout: float = 15.0) -> list[dict]:
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
    ]'''

new = '''def _row_values(data_group: dict) -> list[str | None]:
    """Pull the display_value (or draw_value, for the form column) out of
    one row's data[i] group."""
    out = []
    for item in data_group.get("values", []):
        v = item.get("value", {})
        out.append(v.get("display_value") or v.get("draw_value"))
    return out


def fetch_standings(table_id: str, timeout: float = 15.0) -> list[dict]:
    """Fetch the full league standings table (overall, not home/away split).

    CONFIRMED (2026-09-14) structure: response has "standings_groups"; each
    group has "rows_total" (the complete overall standings — 20 rows for a
    typical league, confirmed complete) alongside "rows_home"/"rows_away"
    splits (not used here). Each row's "data" is 3 groups matching
    "headers_total": [0]=played/goal_difference/points, [1]=wins/draws/
    losses, [2]=form (e.g. "W-W-W-D"). A league may have multiple standings
    groups (e.g. separate group-stage tables) — this uses the first group;
    revisit if a competition needs a specific one instead.
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

    rows = groups[0].get("rows_total", [])
    parsed = []
    for row in rows:
        row_data = row.get("data", [])
        played, goal_diff, points = (_row_values(row_data[0]) + [None, None, None])[:3] if len(row_data) > 0 else (None, None, None)
        wins, draws, losses = (_row_values(row_data[1]) + [None, None, None])[:3] if len(row_data) > 1 else (None, None, None)
        form = _row_values(row_data[2])[0] if len(row_data) > 2 and _row_values(row_data[2]) else None

        parsed.append({
            "rank": row.get("rank"),
            "team_name": row.get("competitor_name"),
            "team_id": row.get("competitor_id"),
            "country_code": row.get("country_code"),
            "played": played,
            "goal_difference": goal_diff,
            "points": points,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "form": form,
        })
    return parsed'''

if old not in content:
    raise SystemExit("ERROR: old fetch_standings not found verbatim — aborting without changes.")
content = content.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched superbet_scraper/stats.py: fetch_standings now uses rows_total with full column parsing.")
PYEOF_ANDREI

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py tools/*.py && echo "OK — syntax valid."
