#!/bin/bash
# fix_fuzzy_matching.sh — [SuperBet repo] replaces the exact-name merge with
# a fuzzy match: a BetExplorer team name and a Superbet team name are
# considered the same team if every word of the SHORTER name appears in the
# LONGER name (e.g. "torino" vs "torino fc", "blackburn u21" vs "blackburn
# rovers u21"). This fixes real matches being missed just because the two
# sites format club names differently (short vs. full/official name).
# Run from the SuperBet repo root: bash fix_fuzzy_matching.sh
set -e

python3 << 'PYEOF_ANDREI'
path = "daily_recommendations.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_match_fn = '''def match_across_sources(high_confidence: pd.DataFrame, superbet: pd.DataFrame) -> pd.DataFrame:
    """Inner-join on normalized (home, away) team names. A match present in
    BetExplorer's 100%-confidence list but NOT found here simply isn't
    (yet, or ever) offered on Superbet for this date/run -- that's a normal
    outcome, not an error, and is reported separately in the email rather
    than silently dropped.

    Both sheets share several column names (League, Home Team, Away Team,
    Odds Over, Match Link) -- pandas suffixes those on merge so we can
    pick the right source per field afterwards (live odds/link from
    Superbet, historical hit-rate counts from BetExplorer).
    """
    merged = high_confidence.merge(
        superbet,
        on=["_home_norm", "_away_norm"],
        how="inner",
        suffixes=(_SUFFIX_BET, _SUFFIX_SB),
    )
    return merged'''

new_match_fn = '''def _names_match(name_a: str, name_b: str) -> bool:
    """Two normalized team names are considered the same team if every word
    of the SHORTER one appears in the LONGER one -- handles BetExplorer and
    Superbet formatting the same club differently, e.g. "torino" vs
    "torino fc", "blackburn u21" vs "blackburn rovers u21". Exact match is
    just the common case of this (identical word sets both ways).
    """
    if not name_a or not name_b:
        return False
    if name_a == name_b:
        return True
    tokens_a, tokens_b = set(name_a.split()), set(name_b.split())
    if not tokens_a or not tokens_b:
        return False
    return tokens_a <= tokens_b or tokens_b <= tokens_a


def match_across_sources(high_confidence: pd.DataFrame, superbet: pd.DataFrame) -> pd.DataFrame:
    """Fuzzy match on normalized (home, away) team names -- see
    _names_match. A BetExplorer match with no fuzzy match on Superbet
    simply isn't (yet, or ever) offered there for this date/run -- that's
    a normal outcome, not an error, and is reported separately in the
    email rather than silently dropped.

    Both sheets can share column names (League, Home Team, Odds Over,
    Match Link, ...) so every field is explicitly suffixed here (not left
    to pandas' merge-suffix mechanism) to keep the source unambiguous --
    live odds/link come from Superbet, historical hit-rate counts from
    BetExplorer.
    """
    matched_rows = []
    for _, bet_row in high_confidence.iterrows():
        candidates = superbet[
            superbet.apply(
                lambda sb_row: _names_match(bet_row["_home_norm"], sb_row["_home_norm"])
                and _names_match(bet_row["_away_norm"], sb_row["_away_norm"]),
                axis=1,
            )
        ]
        if candidates.empty:
            continue
        sb_row = candidates.iloc[0]
        combined = {}
        for col, val in bet_row.items():
            if col in ("_home_norm", "_away_norm"):
                continue
            combined[col + _SUFFIX_BET] = val
        for col, val in sb_row.items():
            if col in ("_home_norm", "_away_norm"):
                continue
            combined[col + _SUFFIX_SB] = val
        matched_rows.append(combined)

    if not matched_rows:
        return pd.DataFrame()
    return pd.DataFrame(matched_rows)'''

if old_match_fn not in content:
    raise SystemExit("ERROR: expected match_across_sources function not found verbatim — aborting without changes.")
content = content.replace(old_match_fn, new_match_fn, 1)

old_body = '''        for _, row in matched.iterrows():
            league = row.get("League" + _SUFFIX_BET, row.get("League", ""))
            home = row.get("Home Team" + _SUFFIX_BET, row.get("Home Team", ""))
            away = row.get("Away Team" + _SUFFIX_BET, row.get("Away Team", ""))
            time_text = row.get("Kick-off Time" + _SUFFIX_BET, row.get("Kick-off Time", ""))
            odds_over = row.get("Odds Over" + _SUFFIX_SB, row.get("Odds Over", ""))
            home_over = row.get("Home Over 2.5 (matches)")
            home_under = row.get("Home Under 2.5 (matches)")
            away_over = row.get("Away Over 2.5 (matches)")
            away_under = row.get("Away Under 2.5 (matches)")
            match_url = row.get("Match Link" + _SUFFIX_SB) or row.get("Match Link" + _SUFFIX_BET) or row.get("Match Link")'''

new_body = '''        for _, row in matched.iterrows():
            league = row.get("League" + _SUFFIX_BET, "")
            home = row.get("Home Team" + _SUFFIX_BET, "")
            away = row.get("Away Team" + _SUFFIX_BET, "")
            time_text = row.get("Kick-off Time" + _SUFFIX_BET, "")
            odds_over = row.get("Odds Over" + _SUFFIX_SB, "")
            home_over = row.get("Home Over 2.5 (matches)" + _SUFFIX_BET)
            home_under = row.get("Home Under 2.5 (matches)" + _SUFFIX_BET)
            away_over = row.get("Away Over 2.5 (matches)" + _SUFFIX_BET)
            away_under = row.get("Away Under 2.5 (matches)" + _SUFFIX_BET)
            match_url = row.get("Match Link" + _SUFFIX_SB) or row.get("Match Link" + _SUFFIX_BET)'''

if old_body not in content:
    raise SystemExit("ERROR: expected build_email_body loop body not found verbatim — aborting without changes.")
content = content.replace(old_body, new_body, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched daily_recommendations.py: fuzzy team-name matching + explicit per-source column suffixes.")
PYEOF_ANDREI

echo "Verifying syntax..."
python3 -c "import ast; ast.parse(open('daily_recommendations.py').read())" && echo "OK -- syntax valid."
