"""Final scores for logged value bets, from the BetExplorer scraper in the `andrei0182/bet` repo.

Same approach as `check_results.py`: run that repo's `main.py --date D --output X.xlsx` and read the
"Final Score" of each match on the "Matches" sheet. Scores are cached per bet (date + Pinnacle team
names) in `value_results.csv`, so each finished match is scraped once. Postponed or not-yet-found
matches stay pending and are retried on later runs, for `retry_days` days.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from .value import _similarity, team_key

CACHE_COLUMNS = ["date", "home", "away", "FTHG", "FTAG", "status", "be_home", "be_away", "checked_at"]


def parse_score(score: object) -> tuple[float, float, str]:
    """BetExplorer "2:1" -> (2, 1, "finished"); "POSTP." -> postponed; anything else -> not available yet."""
    if isinstance(score, str) and ":" in score:
        try:
            hg, ag = (int(x) for x in score.strip().split(":", 1))
            return float(hg), float(ag), "finished"
        except ValueError:
            pass
    if isinstance(score, str) and score.strip().upper().startswith(("POSTP", "CANC", "ABN", "AWA")):
        return np.nan, np.nan, "postponed"
    return np.nan, np.nan, "pending"


def scrape_betexplorer(date: str, bet_repo: str | Path, timeout: int = 900) -> pd.DataFrame | None:
    """The bet repo's scraper for one day -> its "Matches" sheet (None when the scraper fails)."""
    out = Path(tempfile.gettempdir()) / f"value_results_{date}.xlsx"
    try:
        run = subprocess.run([sys.executable, "main.py", "--date", date, "--output", str(out)], cwd=bet_repo,
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  WARNING: BetExplorer scraper failed for {date}: {exc}")
        return None
    if run.returncode != 0 or not out.exists():
        print(f"  WARNING: BetExplorer scraper failed for {date}: {run.stderr.strip()[-500:]}")
        return None
    return pd.read_excel(out, sheet_name="Matches")


def match_scores(bets: pd.DataFrame, day: pd.DataFrame, fuzzy: float = 0.75) -> pd.DataFrame:
    """For each bet (one date), the BetExplorer match with the most similar home and away names."""
    cand = day.assign(_h=day["Home Team"].map(team_key), _a=day["Away Team"].map(team_key))
    rows = []
    for _, b in bets.iterrows():
        h, a = team_key(b["home"]), team_key(b["away"])
        best, best_score = None, fuzzy
        for j, c in cand.iterrows():
            score = min(_similarity(h, c["_h"]), _similarity(a, c["_a"]))
            if score >= best_score:
                best, best_score = j, score
        if best is None:
            rows.append({"date": b["date"], "home": b["home"], "away": b["away"], "FTHG": np.nan, "FTAG": np.nan,
                         "status": "not_found", "be_home": "", "be_away": ""})
            continue
        hg, ag, status = parse_score(cand.loc[best].get("Final Score"))
        rows.append({"date": b["date"], "home": b["home"], "away": b["away"], "FTHG": hg, "FTAG": ag,
                     "status": status, "be_home": cand.loc[best, "Home Team"], "be_away": cand.loc[best, "Away Team"]})
    return pd.DataFrame(rows, columns=CACHE_COLUMNS[:-1])


def read_cache(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    cache = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=CACHE_COLUMNS)
    return cache.assign(date=pd.to_datetime(cache["date"]))


def update_results(log: pd.DataFrame, cache_path: str | Path, bet_repo: str | Path | None,
                   today: pd.Timestamp | None = None, retry_days: int = 14, backfill_days: int = 60,
                   scraper=scrape_betexplorer) -> pd.DataFrame:
    """Scrape scores for past bets without a final score (dates before `today`).

    Bets never checked are scraped up to `backfill_days` back; postponed / not-found ones are retried
    for `retry_days`, then left pending.
    """
    cache = read_cache(cache_path)
    if bet_repo is None or log.empty:
        return cache
    today = (today or pd.Timestamp.now(tz="Europe/Bucharest").tz_localize(None)).normalize()
    keys = log[["date", "home", "away"]].drop_duplicates()
    todo = keys.merge(cache[["date", "home", "away", "status"]].drop_duplicates(["date", "home", "away"]),
                      how="left", on=["date", "home", "away"])
    age = today - todo["date"]
    never = todo["status"].isna() & (age <= pd.Timedelta(days=backfill_days))
    retry = todo["status"].notna() & (todo["status"] != "finished") & (age <= pd.Timedelta(days=retry_days))
    todo = todo[(todo["date"] < today) & (never | retry)].drop(columns="status")
    for date, bets in todo.groupby("date"):
        day = scraper(f"{date:%Y-%m-%d}", bet_repo)
        if day is None:
            continue
        found = match_scores(bets, day).assign(checked_at=f"{pd.Timestamp.now(tz='UTC'):%Y-%m-%dT%H:%MZ}")
        print(f"Rezultate {date:%Y-%m-%d}: {int((found['status'] == 'finished').sum())}/{len(found)} găsite")
        cache = pd.concat([cache.merge(found[["date", "home", "away"]], how="left", indicator=True)
                           .query("_merge == 'left_only'").drop(columns="_merge"), found], ignore_index=True)
    cache = cache.sort_values(["date", "home"], kind="stable")[CACHE_COLUMNS]
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    cache.to_csv(cache_path, index=False)
    return cache


def attach_scores(log: pd.DataFrame, cache: pd.DataFrame) -> pd.DataFrame:
    """FTHG/FTAG on each logged bet from the results cache (finished matches only; the rest stay pending)."""
    done = cache[cache["status"] == "finished"][["date", "home", "away", "FTHG", "FTAG"]]
    done = done.assign(date=pd.to_datetime(done["date"]), FTHG=pd.to_numeric(done["FTHG"]),
                       FTAG=pd.to_numeric(done["FTAG"]))
    out = log.drop(columns=[c for c in ("FTHG", "FTAG") if c in log])
    return out.merge(done.drop_duplicates(["date", "home", "away"]), on=["date", "home", "away"], how="left")
