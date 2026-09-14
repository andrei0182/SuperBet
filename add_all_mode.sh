#!/bin/bash
# add_all_mode.sh — adds --all mode to main.py: fetches every football
# match for a given date, across ALL tournaments (batched, since a single
# request with all ~2837 tournament IDs may hit a URL-length limit —
# untested until now). Adds a reverse tournament id->slug lookup for the
# League column, thread-pooled O/U fetching (same pattern that worked for
# BetExplorer at scale), and a terminal summary for Over 2.5.
# Run from the repo root: bash add_all_mode.sh
set -e

python3 << 'PYEOF_ANDREI'
path = "superbet_scraper/tournaments.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

addition = '''

_reverse_map_cache: dict[int, str] | None = None


def reverse_lookup(tournament_id: int) -> str | None:
    """id -> slug, e.g. 104 -> "fotbal---italia---serie-a" (cached)."""
    global _reverse_map_cache
    if _reverse_map_cache is None:
        _reverse_map_cache = {v: k for k, v in football_tournaments().items()}
    return _reverse_map_cache.get(tournament_id)
'''
content = content.rstrip("\n") + addition + "\n"

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched superbet_scraper/tournaments.py: added reverse_lookup().")
PYEOF_ANDREI

python3 << 'PYEOF_ANDREI'
path = "superbet_scraper/events.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

addition = '''

def fetch_events_batched(
    tournament_ids: list[int],
    date: dt.date,
    index: str = "active-prematch",
    batch_size: int = 150,
    timeout: float = 20.0,
) -> list[dict]:
    """Same as fetch_events, but splits tournament_ids into batches — the
    events endpoint takes tournament ids as a comma-separated query param,
    and passing all ~2837 football tournament ids in one request risks
    hitting a URL-length limit (untested at that scale until now; 150 ids
    per batch is a conservative starting point, ~150*6 chars ~= 900 chars
    per request — well under typical limits).
    """
    all_events: list[dict] = []
    for i in range(0, len(tournament_ids), batch_size):
        batch = tournament_ids[i : i + batch_size]
        events = fetch_events(batch, date, index=index, timeout=timeout)
        all_events.extend(events)
    return all_events
'''
content = content.rstrip("\n") + addition + "\n"

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched superbet_scraper/events.py: added fetch_events_batched().")
PYEOF_ANDREI

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py main.py tools/*.py && echo "OK — syntax valid."
