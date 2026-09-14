from __future__ import annotations

import argparse
import unicodedata

import pandas as pd


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower().strip()
    normalized = "".join(c if c.isalnum() or c.isspace() else " " for c in normalized)
    return " ".join(normalized.split())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bet-xlsx", required=True)
    parser.add_argument("--superbet-xlsx", required=True)
    args = parser.parse_args()

    bet = pd.read_excel(args.bet_xlsx, sheet_name="100% Over 2.5")
    sb = pd.read_excel(args.superbet_xlsx, sheet_name="Matches")

    print(f"=== BetExplorer '100% Over 2.5' today: {len(bet)} matches ===")
    for _, row in bet.iterrows():
        h, a = row["Home Team"], row["Away Team"]
        print(f"  {h!r} vs {a!r}  ->  norm: {normalize_name(h)!r} / {normalize_name(a)!r}   [{row.get('League','')}]")

    print(f"\n=== Superbet today: {len(sb)} matches (all leagues) ===")
    sb_norm_pairs = set()
    for _, row in sb.iterrows():
        h, a = row["Home Team"], row["Away Team"]
        sb_norm_pairs.add((normalize_name(h), normalize_name(a)))

    print(f"\n=== Checking each BetExplorer pair against Superbet's set ===")
    for _, row in bet.iterrows():
        h, a = row["Home Team"], row["Away Team"]
        nh, na = normalize_name(h), normalize_name(a)
        hit = (nh, na) in sb_norm_pairs
        print(f"  {h} vs {a}  ->  {'FOUND on Superbet' if hit else 'not found on Superbet'}")

    # Also show any Superbet team name that *loosely* resembles a BetExplorer
    # team name (shares first 5 chars), to spot near-misses.
    print(f"\n=== Near-miss check (shared first 5 chars of team name) ===")
    bet_teams = set()
    for _, row in bet.iterrows():
        bet_teams.add(normalize_name(row["Home Team"]))
        bet_teams.add(normalize_name(row["Away Team"]))
    sb_teams = set()
    for _, row in sb.iterrows():
        sb_teams.add(normalize_name(row["Home Team"]))
        sb_teams.add(normalize_name(row["Away Team"]))
    for bt in sorted(bet_teams):
        close = [st for st in sb_teams if st[:5] == bt[:5] and st != bt]
        if close:
            print(f"  BetExplorer {bt!r} ~ Superbet {close}")


if __name__ == "__main__":
    main()
