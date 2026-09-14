#!/bin/bash
# fix_tournament_types.sh — fixes two issues discovered testing at scale:
# 1. Tournament map values are STRINGS ("104"), not ints — earlier code
#    happened to work by accident (str() is a no-op on strings) but broke
#    the moment we tried to use them as a set (unhashable dict for the
#    other case).
# 2. ~49 entries are GROUPED tournaments: a dict with "tournamentIds": [...]
#    (multiple underlying ids merged under one display name, e.g. two
#    Norway 2.Division groups shown as one competition). Adds a normalizer
#    that handles both shapes uniformly.
# Run from the repo root: bash fix_tournament_types.sh
set -e

python3 << 'PYEOF_ANDREI'
path = "superbet_scraper/tournaments.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_find = '''def find_tournament(country_slug: str, league_slug: str) -> int | None:
    """Look up one tournament's id by its country and league slug, e.g.
    find_tournament("italia", "serie-a") -> 104. Returns None if not found.
    """
    key = f"fotbal---{country_slug}---{league_slug}"
    return fetch_tournament_map().get(key)'''

new_find = '''def normalize_tournament_value(value: str | dict) -> list[int]:
    """The tournament map's values come in two confirmed shapes (2026-09-14):
    a plain numeric-string id ("104"), or — for ~49 entries — a dict with
    "tournamentIds": [...] (multiple underlying tournament ids merged under
    one display name, e.g. two Norway 2.Division groups shown as a single
    "Norvegia - 2.Division" competition). Always returns a list of ints.
    """
    if isinstance(value, dict):
        return [int(x) for x in value.get("tournamentIds", [])]
    return [int(value)]


def find_tournament(country_slug: str, league_slug: str) -> int | None:
    """Look up one tournament's id by its country and league slug, e.g.
    find_tournament("italia", "serie-a") -> 104. Returns None if not found.

    For a GROUPED entry (see normalize_tournament_value), returns only the
    first underlying id — use find_tournament_ids() instead if you need all
    of them (e.g. to fetch every match under a merged competition name).
    """
    key = f"fotbal---{country_slug}---{league_slug}"
    value = fetch_tournament_map().get(key)
    if value is None:
        return None
    ids = normalize_tournament_value(value)
    return ids[0] if ids else None


def find_tournament_ids(country_slug: str, league_slug: str) -> list[int]:
    """Like find_tournament, but returns ALL underlying ids for a grouped
    entry instead of just the first one. For a non-grouped entry, returns a
    single-item list."""
    key = f"fotbal---{country_slug}---{league_slug}"
    value = fetch_tournament_map().get(key)
    return normalize_tournament_value(value) if value is not None else []


def all_football_tournament_ids() -> list[int]:
    """Every underlying tournament id across all football entries (grouped
    entries expanded, duplicates removed) — for an "--all leagues" fetch."""
    ids: set[int] = set()
    for value in football_tournaments().values():
        ids.update(normalize_tournament_value(value))
    return sorted(ids)'''

if old_find not in content:
    raise SystemExit("ERROR: old find_tournament not found verbatim — aborting without changes.")
content = content.replace(old_find, new_find, 1)

# reverse_lookup needs updating too, since values are no longer plain ints
old_reverse = '''_reverse_map_cache: dict[int, str] | None = None


def reverse_lookup(tournament_id: int) -> str | None:
    """id -> slug, e.g. 104 -> "fotbal---italia---serie-a" (cached)."""
    global _reverse_map_cache
    if _reverse_map_cache is None:
        _reverse_map_cache = {v: k for k, v in football_tournaments().items()}
    return _reverse_map_cache.get(tournament_id)'''

new_reverse = '''_reverse_map_cache: dict[int, str] | None = None


def reverse_lookup(tournament_id: int) -> str | None:
    """id -> slug, e.g. 104 -> "fotbal---italia---serie-a" (cached). For a
    grouped entry, every underlying id maps back to the same slug."""
    global _reverse_map_cache
    if _reverse_map_cache is None:
        _reverse_map_cache = {}
        for slug, value in football_tournaments().items():
            for tid in normalize_tournament_value(value):
                _reverse_map_cache[tid] = slug
    return _reverse_map_cache.get(tournament_id)'''

if old_reverse not in content:
    raise SystemExit("ERROR: old reverse_lookup not found verbatim — aborting without changes.")
content = content.replace(old_reverse, new_reverse, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched superbet_scraper/tournaments.py: fixed str/dict value handling, added find_tournament_ids() and all_football_tournament_ids().")
PYEOF_ANDREI

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py main.py tools/*.py && echo "OK — syntax valid."
