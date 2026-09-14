#!/bin/bash
set -e

cat > check_results.py << 'EOF'
"""Checks recommendations_log.csv for past picks still marked "pending" and
fills in what actually happened, by re-scraping BetExplorer's completed-
results page for each pending pick's date and reading the real final score.

Run BEFORE generating today's new recommendations (see the GitHub Actions
workflow) so the email's running accuracy summary is up to date. Safe to
run multiple times -- already-resolved rows are skipped, and a match not
found/not yet played is simply left "pending" for next time.

Requires the `bet` repo checked out as a sibling directory (../bet), since
it reuses that project's own scraper rather than re-implementing it.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import unicodedata
from datetime import date as Date
from pathlib import Path

import pandas as pd

LOG_PATH = "recommendations_log.csv"
LOG_COLUMNS = [
    "date", "league", "home_team", "away_team", "odds_over",
    "kickoff_local", "result", "total_goals", "checked_at",
]


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower().strip()
    normalized = "".join(c if c.isalnum() or c.isspace() else " " for c in normalized)
    return " ".join(normalized.split())


def names_match(name_a: str, name_b: str) -> bool:
    if not name_a or not name_b:
        return False
    if name_a == name_b:
        return True
    tokens_a, tokens_b = set(name_a.split()), set(name_b.split())
    if not tokens_a or not tokens_b:
        return False
    return tokens_a <= tokens_b or tokens_b <= tokens_a


def load_log() -> pd.DataFrame:
    path = Path(LOG_PATH)
    if not path.exists():
        return pd.DataFrame(columns=LOG_COLUMNS)
    return pd.read_csv(path, dtype=str)


def save_log(df: pd.DataFrame) -> None:
    df.to_csv(LOG_PATH, index=False)


def scrape_bet_date(date_str: str, bet_repo: str) -> pd.DataFrame | None:
    out_path = f"/tmp/results_check_{date_str}.xlsx"
    cmd = [sys.executable, "main.py", "--date", date_str, "--output", out_path]
    result = subprocess.run(cmd, cwd=bet_repo, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  WARNING: bet scraper failed for {date_str}: {result.stderr.strip()[-500:]}")
        return None
    if not Path(out_path).exists():
        print(f"  WARNING: bet scraper produced no output file for {date_str}.")
        return None
    return pd.read_excel(out_path, sheet_name="Matches")


def resolve_pick(row: pd.Series, day_matches: pd.DataFrame) -> tuple[str, str]:
    home_norm = normalize_name(row["home_team"])
    away_norm = normalize_name(row["away_team"])
    for _, m in day_matches.iterrows():
        if names_match(home_norm, normalize_name(m["Home Team"])) and names_match(away_norm, normalize_name(m["Away Team"])):
            score = m.get("Final Score")
            if not isinstance(score, str) or ":" not in score:
                if isinstance(score, str) and score.strip().upper().startswith("POSTP"):
                    return "postponed", ""
                return "no_data", ""
            try:
                home_goals, away_goals = (int(x) for x in score.split(":", 1))
            except ValueError:
                return "no_data", ""
            total = home_goals + away_goals
            return ("over" if total > 2.5 else "under"), str(total)
    return "no_data", ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bet-repo", default="../bet", help="Path to the bet repo (checked out as a sibling by default).")
    args = parser.parse_args()

    log = load_log()
    if log.empty:
        print("No log yet (recommendations_log.csv doesn't exist or is empty) -- nothing to check.")
        return

    today_str = Date.today().isoformat()
    pending = log[(log["result"] == "pending") & (log["date"] < today_str)]
    if pending.empty:
        print("No past-dated pending picks to check.")
        return

    pending_dates = sorted(pending["date"].unique())
    print(f"Checking {len(pending)} pending pick(s) across {len(pending_dates)} date(s): {pending_dates}")

    for date_str in pending_dates:
        print(f"Scraping BetExplorer results for {date_str}...")
        day_matches = scrape_bet_date(date_str, args.bet_repo)
        if day_matches is None:
            continue
        idxs = log.index[(log["result"] == "pending") & (log["date"] == date_str)]
        for idx in idxs:
            result, total_goals = resolve_pick(log.loc[idx], day_matches)
            if result == "no_data":
                continue
            log.loc[idx, "result"] = result
            log.loc[idx, "total_goals"] = total_goals
            log.loc[idx, "checked_at"] = Date.today().isoformat()
            print(f"  {log.loc[idx, 'home_team']} vs {log.loc[idx, 'away_team']} ({date_str}): {result} ({total_goals} goals)")

    save_log(log)
    resolved = log[log["result"].isin(["over", "under"])]
    if not resolved.empty:
        hit_rate = (resolved["result"] == "over").mean()
        print(f"\nOverall so far: {len(resolved)} confirmed picks, {hit_rate:.0%} went Over 2.5.")


if __name__ == "__main__":
    main()
EOF

echo "check_results.py created."

python3 << 'PYEOF_ANDREI'
path = "daily_recommendations.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_imports = "import pandas as pd\n\n_SUFFIX_BET"
new_imports = "import pandas as pd\n\nLOG_PATH = \"recommendations_log.csv\"\nLOG_COLUMNS = [\n    \"date\", \"league\", \"home_team\", \"away_team\", \"odds_over\",\n    \"kickoff_local\", \"result\", \"total_goals\", \"checked_at\",\n]\n\n_SUFFIX_BET"
if old_imports not in content:
    raise SystemExit("ERROR: expected imports anchor not found verbatim — aborting without changes.")
content = content.replace(old_imports, new_imports, 1)

old_marker = "def build_email_body(matched: pd.DataFrame, unmatched_count: int, date_str: str) -> str:"
new_helpers = '''def log_todays_picks(matched: pd.DataFrame, date_str: str) -> None:
    """Appends today's matched picks to the persistent results log."""
    path = Path(LOG_PATH)
    if path.exists():
        log = pd.read_csv(path, dtype=str)
    else:
        log = pd.DataFrame(columns=LOG_COLUMNS)

    new_rows = []
    for _, row in matched.iterrows():
        home = row.get("Home Team" + _SUFFIX_BET, "")
        away = row.get("Away Team" + _SUFFIX_BET, "")
        already_logged = ((log["date"] == date_str) & (log["home_team"] == home) & (log["away_team"] == away)).any()
        if already_logged:
            continue
        new_rows.append({
            "date": date_str,
            "league": row.get("League" + _SUFFIX_BET, ""),
            "home_team": home,
            "away_team": away,
            "odds_over": row.get("Odds Over" + _SUFFIX_SB, ""),
            "kickoff_local": row.get("_kickoff_local" + _SUFFIX_SB, ""),
            "result": "pending",
            "total_goals": "",
            "checked_at": "",
        })
    if new_rows:
        log = pd.concat([log, pd.DataFrame(new_rows)], ignore_index=True)
        log.to_csv(path, index=False)


def accuracy_summary_html() -> str:
    path = Path(LOG_PATH)
    if not path.exists():
        return ""
    log = pd.read_csv(path, dtype=str)
    resolved = log[log["result"].isin(["over", "under"])]
    if resolved.empty:
        return ""
    hit_rate = (resolved["result"] == "over").mean()
    return (
        f"<p style='margin-top:20px; padding-top:10px; border-top:1px solid #ddd; color:#555;'>"
        f"<b>Statistica reala pana acum:</b> din {len(resolved)} recomandari confirmate, "
        f"{(resolved['result'] == 'over').sum()} au fost Peste 2.5 ({hit_rate:.0%}).</p>"
    )


def build_email_body(matched: pd.DataFrame, unmatched_count: int, date_str: str) -> str:'''

if old_marker not in content:
    raise SystemExit("ERROR: expected build_email_body def not found verbatim — aborting without changes.")
content = content.replace(old_marker, new_helpers, 1)

old_top_imports = "from zoneinfo import ZoneInfo\n\nimport pandas as pd"
new_top_imports = "from pathlib import Path\nfrom zoneinfo import ZoneInfo\n\nimport pandas as pd"
if old_top_imports not in content:
    raise SystemExit("ERROR: expected zoneinfo import line not found verbatim — aborting without changes.")
content = content.replace(old_top_imports, new_top_imports, 1)

old_return = '''    if unmatched_count:
        lines.append(
            f"<p style='color:#888; font-size:0.9em;'>Nota: inca {unmatched_count} meciuri au istoric 100% "
            "conform BetExplorer, dar nu au fost gasite (inca) pe Superbet.ro azi.</p>"
        )

    return "\\n".join(lines)'''
new_return = '''    if unmatched_count:
        lines.append(
            f"<p style='color:#888; font-size:0.9em;'>Nota: inca {unmatched_count} meciuri au istoric 100% "
            "conform BetExplorer, dar nu au fost gasite (inca) pe Superbet.ro azi.</p>"
        )

    lines.append(accuracy_summary_html())

    return "\\n".join(lines)'''
if old_return not in content:
    raise SystemExit("ERROR: expected build_email_body return block not found verbatim — aborting without changes.")
content = content.replace(old_return, new_return, 1)

old_main_tail = '''    if args.dry_run:
        print(subject)
        print(body)
        return

    if matched.empty and unmatched_count == 0:
        print("No high-confidence matches at all today -- skipping email.")
        return

    send_email(subject, body)
    print(f"Email sent: {subject}")'''
new_main_tail = '''    if args.dry_run:
        print(subject)
        print(body)
        return

    log_todays_picks(matched, args.date)

    if matched.empty and unmatched_count == 0:
        print("No high-confidence matches at all today -- skipping email.")
        return

    send_email(subject, body)
    print(f"Email sent: {subject}")'''
if old_main_tail not in content:
    raise SystemExit("ERROR: expected main() tail block not found verbatim — aborting without changes.")
content = content.replace(old_main_tail, new_main_tail, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched daily_recommendations.py: logs today's picks + shows running accuracy in the email.")
PYEOF_ANDREI

echo "Verifying syntax..."
python3 -c "import ast; ast.parse(open('daily_recommendations.py').read())" && echo "OK -- daily_recommendations.py syntax valid."
python3 -c "import ast; ast.parse(open('check_results.py').read())" && echo "OK -- check_results.py syntax valid."

python3 << 'PYEOF_ANDREI'
path = ".github/workflows/daily-recommendations.yml"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_header = 'name: Daily 100% Over 2.5 recommendations\n\non:'
new_header = 'name: Daily 100% Over 2.5 recommendations\n\npermissions:\n  contents: write  # needed to commit recommendations_log.csv back to the repo\n\non:'
if old_header not in content:
    raise SystemExit("ERROR: expected workflow header not found verbatim — aborting without changes.")
content = content.replace(old_header, new_header, 1)

old_step = '''      - name: Run BetExplorer scraper (with stats)
        if: env.SKIP_RUN != 'true'
        working-directory: bet
        run: python main.py --date ${{ steps.date.outputs.value }} --with-stats'''
new_step = '''      - name: Check pending results from previous days
        if: env.SKIP_RUN != 'true'
        working-directory: SuperBet
        run: python check_results.py --bet-repo ../bet

      - name: Run BetExplorer scraper (with stats)
        if: env.SKIP_RUN != 'true'
        working-directory: bet
        run: python main.py --date ${{ steps.date.outputs.value }} --with-stats'''
if old_step not in content:
    raise SystemExit("ERROR: expected 'Run BetExplorer scraper' step not found verbatim — aborting without changes.")
content = content.replace(old_step, new_step, 1)

old_tail = '''        run: |
          python daily_recommendations.py \\\\
            --bet-xlsx ../bet/output/matches.xlsx \\\\
            --superbet-xlsx output/matches.xlsx \\\\
            --date ${{ steps.date.outputs.value }}'''
new_tail = '''        run: |
          python daily_recommendations.py \\\\
            --bet-xlsx ../bet/output/matches.xlsx \\\\
            --superbet-xlsx output/matches.xlsx \\\\
            --date ${{ steps.date.outputs.value }}

      - name: Commit updated results log
        if: env.SKIP_RUN != 'true'
        working-directory: SuperBet
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add recommendations_log.csv
          git diff --cached --quiet && echo "No log changes to commit." || git commit -m "Update recommendations log ($(date +%F))"
          git push'''
if old_tail not in content:
    raise SystemExit("ERROR: expected daily_recommendations.py run tail not found verbatim — aborting without changes.")
content = content.replace(old_tail, new_tail, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched workflow: checks pending results first, commits the log back to the repo at the end.")
PYEOF_ANDREI
