#!/bin/bash
# finalize.sh — wires everything together: extends Match with standings/h2h
# fields, adds main.py (CLI: --date + --tournament slug, optional
# --with-stats for O/U + standings + h2h), and export.py (styled Excel,
# same pattern as the BetExplorer project).
# Run from the repo root: bash finalize.sh
set -e

cat > superbet_scraper/models.py << 'EOF'
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Odds1X2:
    home: Optional[float] = None
    draw: Optional[float] = None
    away: Optional[float] = None


@dataclass
class OddsOverUnder:
    line: float = 2.5
    over: Optional[float] = None
    under: Optional[float] = None


@dataclass
class Match:
    league: str
    home_team: str
    away_team: str
    time_text: str
    status: str  # "NOT_STARTED" | other values seen in inplay_stats_metadata.status — confirm full set
    event_id: Optional[int] = None
    odds_1x2: Odds1X2 = field(default_factory=Odds1X2)
    odds_ou: OddsOverUnder = field(default_factory=OddsOverUnder)
    stats_available: bool = False
    home_rank: Optional[int] = None
    home_points: Optional[str] = None
    home_form: Optional[str] = None
    away_rank: Optional[int] = None
    away_points: Optional[str] = None
    away_form: Optional[str] = None
    h2h_home_wins: Optional[int] = None
    h2h_draws: Optional[int] = None
    h2h_away_wins: Optional[int] = None
    h2h_since: Optional[int] = None
    match_url: Optional[str] = None

    def to_flat_dict(self) -> dict:
        return {
            "league": self.league,
            "time": self.time_text,
            "status": self.status,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "odds_1": self.odds_1x2.home,
            "odds_x": self.odds_1x2.draw,
            "odds_2": self.odds_1x2.away,
            "ou_line": self.odds_ou.line,
            "odds_over": self.odds_ou.over,
            "odds_under": self.odds_ou.under,
            "stats_available": self.stats_available,
            "home_rank": self.home_rank,
            "home_points": self.home_points,
            "home_form": self.home_form,
            "away_rank": self.away_rank,
            "away_points": self.away_points,
            "away_form": self.away_form,
            "h2h_home_wins": self.h2h_home_wins,
            "h2h_draws": self.h2h_draws,
            "h2h_away_wins": self.h2h_away_wins,
            "h2h_since": self.h2h_since,
            "match_url": self.match_url,
        }
EOF

cat > superbet_scraper/export.py << 'EOF'
from __future__ import annotations

import os

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from .models import Match

_COLUMN_LABELS = {
    "league": "League",
    "time": "Kick-off Time",
    "status": "Status",
    "home_team": "Home Team",
    "away_team": "Away Team",
    "odds_1": "Odds 1",
    "odds_x": "Odds X",
    "odds_2": "Odds 2",
    "ou_line": "O/U Line",
    "odds_over": "Odds Over",
    "odds_under": "Odds Under",
    "stats_available": "Stats Available",
    "home_rank": "Home Rank",
    "home_points": "Home Points",
    "home_form": "Home Form",
    "away_rank": "Away Rank",
    "away_points": "Away Points",
    "away_form": "Away Form",
    "h2h_home_wins": "H2H Home Wins",
    "h2h_draws": "H2H Draws",
    "h2h_away_wins": "H2H Away Wins",
    "h2h_since": "H2H Since (Year)",
    "match_url": "Match Link",
}

_ODDS_COLUMNS = {"odds_1", "odds_x", "odds_2", "odds_over", "odds_under"}


def matches_to_dataframe(matches: list[Match]) -> pd.DataFrame:
    return pd.DataFrame(m.to_flat_dict() for m in matches)


def _style_sheet(ws, df: pd.DataFrame, odds_cols: set[str]) -> None:
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")

    for col_idx, col_key in enumerate(df.columns, start=1):
        label = _COLUMN_LABELS.get(col_key, col_key)
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        letter = get_column_letter(col_idx)
        sample_values = [str(v) if pd.notna(v) else "" for v in df[col_key].head(200).tolist()]
        max_len = max([len(label)] + [len(v) for v in sample_values])
        ws.column_dimensions[letter].width = min(max(max_len + 2, 10), 42)

        if col_key in odds_cols:
            for row_idx in range(2, len(df) + 2):
                ws.cell(row=row_idx, column=col_idx).number_format = "0.00"

    ws.freeze_panes = "A2"

    if len(df) > 0:
        last_col_letter = get_column_letter(len(df.columns))
        table_ref = f"A1:{last_col_letter}{len(df) + 1}"
        table = Table(displayName="MatchesTable", ref=table_ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False,
            showRowStripes=True, showColumnStripes=False,
        )
        ws.add_table(table)


def save_to_excel(matches: list[Match], path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    df = matches_to_dataframe(matches)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Matches", index=False, header=False, startrow=1)
        _style_sheet(writer.sheets["Matches"], df, _ODDS_COLUMNS)
EOF

cat > main.py << 'EOF'
from __future__ import annotations

import argparse
import datetime as dt
import logging

from superbet_scraper.events import fetch_event_detail, fetch_events, parse_event, parse_over_under
from superbet_scraper.export import save_to_excel
from superbet_scraper.stats import (
    extract_table_id,
    extract_team_ids,
    fetch_fixture_overview,
    fetch_h2h,
    fetch_standings,
)
from superbet_scraper.tournaments import find_tournament, search_tournaments


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape odds + stats for a Superbet.ro football tournament/date.")
    parser.add_argument("--date", type=str, default=dt.date.today().isoformat(), help="YYYY-MM-DD (default: today)")
    parser.add_argument("--tournament", type=str, help='Tournament slug, e.g. "italia/serie-a" (country/league)')
    parser.add_argument("--search", type=str, help='List tournaments matching this text and exit (e.g. "italia")')
    parser.add_argument("--with-stats", action="store_true", help="Also fetch Over/Under odds, standings, and head-to-head history for each match.")
    parser.add_argument("--output", type=str, default="output/matches.xlsx", help="Output Excel path")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    if args.search:
        results = search_tournaments(args.search)
        print(f"Found {len(results)} tournaments matching '{args.search}':")
        for slug, tid in sorted(results.items()):
            print(f"  {slug} -> {tid}")
        return

    if not args.tournament:
        parser.error("--tournament is required (or use --search to find the right slug first)")

    country_slug, _, league_slug = args.tournament.partition("/")
    tournament_id = find_tournament(country_slug, league_slug)
    if tournament_id is None:
        logging.error(
            "Tournament slug '%s' not found. Try: python main.py --search \"%s\"",
            args.tournament, country_slug,
        )
        return

    date = dt.date.fromisoformat(args.date)
    logging.info("Fetching events for tournament_id=%d (%s) on %s", tournament_id, args.tournament, date)

    raw_events = fetch_events([tournament_id], date)
    logging.info("Found %d matches.", len(raw_events))

    matches = []
    standings_cache: dict[str, list[dict]] = {}

    for i, raw_event in enumerate(raw_events, start=1):
        match = parse_event(raw_event)
        match.league = args.tournament
        match.event_id = raw_event.get("event_id")
        logging.info("[%d/%d] %s vs %s", i, len(raw_events), match.home_team, match.away_team)

        if args.with_stats and match.event_id:
            try:
                detail = fetch_event_detail(match.event_id)
                if detail:
                    match.odds_ou = parse_over_under(detail, line=2.5)

                overview = fetch_fixture_overview(match.event_id)
                if overview:
                    table_id = extract_table_id(overview)
                    team1_id, team2_id = extract_team_ids(overview)

                    if table_id:
                        if table_id not in standings_cache:
                            standings_cache[table_id] = fetch_standings(table_id)
                        standings = standings_cache[table_id]
                        by_id = {row["team_id"]: row for row in standings}

                        # ASSUMPTION (unconfirmed beyond one manual check): fixture
                        # overview's team1 == the home team, team2 == away. Revisit
                        # if home/away ever look swapped in output.
                        home_row = by_id.get(team1_id)
                        away_row = by_id.get(team2_id)
                        if home_row:
                            match.home_rank = home_row.get("rank")
                            match.home_points = home_row.get("points")
                            match.home_form = home_row.get("form")
                        if away_row:
                            match.away_rank = away_row.get("rank")
                            match.away_points = away_row.get("points")
                            match.away_form = away_row.get("form")
                        match.stats_available = bool(home_row or away_row)

                    if team1_id and team2_id:
                        h2h = fetch_h2h(team1_id, team2_id)
                        if h2h:
                            stats = h2h.get("h2h_statistics", {})
                            match.h2h_home_wins = stats.get("team1")
                            match.h2h_draws = stats.get("draw")
                            match.h2h_away_wins = stats.get("team2")
                            match.h2h_since = h2h.get("h2h_year_since")
            except Exception:
                logging.exception("Failed to fetch stats for %s vs %s — leaving stats blank.", match.home_team, match.away_team)

        matches.append(match)

    save_to_excel(matches, args.output)
    logging.info("Saved %s (%d matches).", args.output, len(matches))


if __name__ == "__main__":
    main()
EOF

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py main.py tools/*.py && echo "OK — syntax valid."
