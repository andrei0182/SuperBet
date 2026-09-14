#!/bin/bash
# diagnose_times.sh — [SuperBet repo] one-off diagnostic: prints the raw
# "Kick-off Time" values as stored in both exported Excel files, for the
# matches that showed up in the daily recommendation email, so we can see
# the exact string format before deciding how to convert/display them
# correctly (BetExplorer's displayed time is documented as unreliable —
# see betscraper/selectors.py's data-dt comment).
# Run from the SuperBet repo root:
#   python diagnose_times.py --bet-xlsx ../bet/output/matches.xlsx --superbet-xlsx output/matches.xlsx
set -e

cat > diagnose_times.py << 'EOF'
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

    print("=== BetExplorer 'Kick-off Time' raw values (100% Over 2.5 sheet) ===")
    for _, row in bet.iterrows():
        print(f"  {row['Home Team']} vs {row['Away Team']}: {row['Kick-off Time']!r}  (dtype-repr shown above)")

    print("\n=== BetExplorer 'Match Date' raw values (same sheet, if present) ===")
    if "Match Date" in bet.columns:
        for _, row in bet.iterrows():
            print(f"  {row['Home Team']} vs {row['Away Team']}: {row['Match Date']!r}")
    else:
        print("  (no 'Match Date' column in this sheet)")

    print("\n=== Superbet 'Kick-off Time' raw values, for the 3 teams from the email ===")
    targets = {"krasava", "blackburn", "nottingham"}
    for _, row in sb.iterrows():
        h, a = normalize_name(row["Home Team"]), normalize_name(row["Away Team"])
        if any(t in h or t in a for t in targets):
            print(f"  {row['Home Team']} vs {row['Away Team']}: {row['Kick-off Time']!r}  (type: {type(row['Kick-off Time']).__name__})")


if __name__ == "__main__":
    main()
EOF

echo "diagnose_times.py created. Run it with:"
echo "  python diagnose_times.py --bet-xlsx ../bet/output/matches.xlsx --superbet-xlsx output/matches.xlsx"
