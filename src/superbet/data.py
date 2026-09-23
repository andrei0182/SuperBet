"""Load and clean football-data.co.uk CSV files."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

BTTS_FILENAME = "btts_odds.csv"

# Bet-time odds: preferred source first, then fallbacks.
ODDS_SOURCES: dict[str, dict[str, str]] = {
    "B365": {
        "odds_h": "B365H", "odds_d": "B365D", "odds_a": "B365A",
        "odds_over": "B365>2.5", "odds_under": "B365<2.5",
    },
    "Avg": {
        "odds_h": "AvgH", "odds_d": "AvgD", "odds_a": "AvgA",
        "odds_over": "Avg>2.5", "odds_under": "Avg<2.5",
    },
}

# Closing Pinnacle odds: used ONLY for CLV, never as a model/blend input.
CLOSING_COLUMNS: dict[str, str] = {
    "close_h": "PSCH", "close_d": "PSCD", "close_a": "PSCA",
    "close_over": "PC>2.5", "close_under": "PC<2.5",
}

OUTPUT_COLUMNS = [
    "date", "league", "home", "away", "hg", "ag",
    "odds_h", "odds_d", "odds_a", "odds_over", "odds_under",
    "close_h", "close_d", "close_a", "close_over", "close_under",
]


def normalize_team(name: object) -> str:
    """Canonical team key: trimmed, single-spaced, unchanged case."""
    return re.sub(r"\s+", " ", str(name)).strip()


def parse_dates(values: pd.Series) -> pd.Series:
    """Parse football-data dates (dd/mm/yy or dd/mm/yyyy)."""
    text = values.astype(str).str.strip()
    long = pd.to_datetime(text, format="%d/%m/%Y", errors="coerce")
    short = pd.to_datetime(text, format="%d/%m/%y", errors="coerce")
    iso = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    return long.fillna(short).fillna(iso)


def _read_csv(path: Path) -> pd.DataFrame:
    """Read a CSV trying utf-8 first, then latin-1 (older football-data files)."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(path, encoding=encoding, on_bad_lines="skip")
        except UnicodeDecodeError:
            continue
    raise ValueError(f"cannot decode {path}")


def _pick_odds(raw: pd.DataFrame, odds_source: str) -> pd.DataFrame:
    """Bet-time odds from the preferred source, falling back per column."""
    order = [odds_source] + [s for s in ODDS_SOURCES if s != odds_source]
    out = pd.DataFrame(index=raw.index)
    for target in ODDS_SOURCES[odds_source]:
        col = pd.Series(np.nan, index=raw.index)
        for source in order:
            name = ODDS_SOURCES[source][target]
            if name in raw.columns:
                col = col.fillna(pd.to_numeric(raw[name], errors="coerce"))
        out[target] = col
    return out


def clean_frame(raw: pd.DataFrame, odds_source: str = "B365", league: str | None = None,
                require_scores: bool = True) -> pd.DataFrame:
    """Convert one raw football-data frame into the standard schema.

    With `require_scores=False` (fixtures) rows without a score are kept.
    """
    required = {"Date", "HomeTeam", "AwayTeam"} | ({"FTHG", "FTAG"} if require_scores else set())
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    df = pd.DataFrame({
        "date": parse_dates(raw["Date"]),
        "league": raw["Div"].astype(str) if "Div" in raw.columns else (league or "UNK"),
        "home": raw["HomeTeam"].map(normalize_team),
        "away": raw["AwayTeam"].map(normalize_team),
        "hg": pd.to_numeric(raw["FTHG"], errors="coerce") if "FTHG" in raw.columns else np.nan,
        "ag": pd.to_numeric(raw["FTAG"], errors="coerce") if "FTAG" in raw.columns else np.nan,
    })
    df = pd.concat([df, _pick_odds(raw, odds_source)], axis=1)
    for target, name in CLOSING_COLUMNS.items():
        df[target] = pd.to_numeric(raw[name], errors="coerce") if name in raw.columns else np.nan
    odds_cols = list(ODDS_SOURCES[odds_source]) + list(CLOSING_COLUMNS)
    df[odds_cols] = df[odds_cols].where(df[odds_cols] > 1.0)  # 0 / <=1 means missing in the source files
    df = df[(df["home"] != "nan") & (df["away"] != "nan")]
    if not require_scores:
        return df.dropna(subset=["date"])[OUTPUT_COLUMNS]
    df = df.dropna(subset=["date", "hg", "ag"])
    df["hg"] = df["hg"].astype(int)
    df["ag"] = df["ag"].astype(int)
    return df[OUTPUT_COLUMNS]


def load_matches(data_dir: str | Path, odds_source: str = "B365") -> pd.DataFrame:
    """Load every football-data CSV in `data_dir`, cleaned and sorted by date."""
    data_dir = Path(data_dir)
    files = sorted(p for p in data_dir.glob("*.csv") if p.name != BTTS_FILENAME)
    if not files:
        raise FileNotFoundError(f"no CSV files in {data_dir}")
    frames = [clean_frame(_read_csv(p), odds_source, league=p.stem.split("_")[0]) for p in files]
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["date", "league", "home", "away"], keep="last")
    return df.sort_values(["date", "league", "home"], kind="stable").reset_index(drop=True)


def load_fixtures(path: str | Path, odds_source: str = "B365") -> pd.DataFrame:
    """Load upcoming fixtures (Date, HomeTeam, AwayTeam, optional Div and odds columns)."""
    raw = _read_csv(Path(path))
    df = clean_frame(raw, odds_source, league="UNK", require_scores=False)
    if "Div" not in raw.columns:
        df["league"] = None
    df = df.drop(columns=["hg", "ag"])
    for name, col in (("odds_gg", "gg_yes"), ("odds_ng", "gg_no")):
        df[name] = pd.to_numeric(raw.loc[df.index, col], errors="coerce") if col in raw.columns else np.nan
    return df.reset_index(drop=True)


def load_btts(path: str | Path) -> pd.DataFrame | None:
    """Load optional GG/NG odds file (Date, HomeTeam, AwayTeam, gg_yes, gg_no)."""
    path = Path(path)
    if not path.exists():
        return None
    raw = _read_csv(path)
    return pd.DataFrame({
        "date": parse_dates(raw["Date"]),
        "home": raw["HomeTeam"].map(normalize_team),
        "away": raw["AwayTeam"].map(normalize_team),
        "odds_gg": pd.to_numeric(raw["gg_yes"], errors="coerce"),
        "odds_ng": pd.to_numeric(raw["gg_no"], errors="coerce"),
    }).dropna(subset=["date"])


def attach_btts(matches: pd.DataFrame, btts: pd.DataFrame | None) -> pd.DataFrame:
    """Left-join GG odds onto matches (NaN where missing)."""
    if btts is None:
        out = matches.copy()
        out["odds_gg"] = np.nan
        out["odds_ng"] = np.nan
        return out
    btts = btts.drop_duplicates(subset=["date", "home", "away"], keep="last")
    return matches.merge(btts, on=["date", "home", "away"], how="left")
