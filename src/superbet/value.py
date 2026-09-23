"""Value betting against a sharp bookmaker's early odds, tracked by closing line value.

No model: the fair probability is the de-vigged sharp price (Pinnacle by
default). A soft bookmaker's price is a value bet when price * fair - 1 >= ev_min.
Closing sharp odds are used only to evaluate bets (CLV, closing EV), never to pick them.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from .blend import devig
from .data import BTTS_FILENAME, _read_csv, normalize_team, parse_dates
from .staking import StakingConfig, stake_size

# football-data.co.uk column names; a soft book "XX" is read from XXH/XXD/XXA and XX>2.5/XX<2.5.
MARKETS: dict[str, dict] = {
    "1x2": {"suffixes": ["H", "D", "A"], "labels": ["1", "X", "2"],
            "sharp": ["PSH", "PSD", "PSA"], "close": ["PSCH", "PSCD", "PSCA"]},
    "ou25": {"suffixes": [">2.5", "<2.5"], "labels": ["Over 2.5", "Under 2.5"],
             "sharp": ["P>2.5", "P<2.5"], "close": ["PC>2.5", "PC<2.5"]},
}
KEY = ["date", "home", "away"]
LOG_COLUMNS = KEY + ["market", "selection", "book", "odds", "fair_p", "fair_odds", "ev", "stake"]


def team_key(name: object) -> str:
    """Loose team key for joining sources: no accents, case, punctuation or club suffixes."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\b(fc|cf|afc|sc|ac|cd|fk|sk|as|ss|us|the)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _odds(df: pd.DataFrame, col: str) -> np.ndarray:
    """Numeric odds column; missing or <= 1 becomes NaN."""
    if col not in df.columns:
        return np.full(len(df), np.nan)
    s = pd.to_numeric(df[col], errors="coerce")
    return s.where(s > 1.0).to_numpy(dtype=float)


def load_odds_table(path: str | Path) -> pd.DataFrame:
    """One CSV/XLSX file, or every CSV in a folder, in football-data layout (Date, HomeTeam, AwayTeam, odds...)."""
    path = Path(path)
    if path.is_dir():
        files = sorted(p for p in path.glob("*.csv") if p.name != BTTS_FILENAME)
        raw = pd.concat([_read_csv(p) for p in files], ignore_index=True)
    elif path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path)
    else:
        raw = _read_csv(path)
    raw = raw.copy()
    dates = raw["Date"]
    raw["date"] = dates.dt.normalize() if pd.api.types.is_datetime64_any_dtype(dates) else parse_dates(dates.astype(str))
    raw["home"] = raw["HomeTeam"].map(normalize_team)
    raw["away"] = raw["AwayTeam"].map(normalize_team)
    return raw.dropna(subset=["date"]).reset_index(drop=True)


def load_superbet_excel(path: str | Path, date: str, book: str = "SB") -> pd.DataFrame:
    """Superbet scraper export (output/matches.xlsx) -> Date, HomeTeam, AwayTeam, SBH, SBD, SBA, SB>2.5, SB<2.5."""
    raw = pd.read_excel(path)
    ou_ok = pd.to_numeric(raw.get("O/U Line"), errors="coerce") == 2.5
    return pd.DataFrame({
        "Date": pd.Timestamp(date).strftime("%d/%m/%Y"),
        "HomeTeam": raw["Home Team"], "AwayTeam": raw["Away Team"],
        f"{book}H": raw["Odds 1"], f"{book}D": raw["Odds X"], f"{book}A": raw["Odds 2"],
        f"{book}>2.5": raw["Odds Over"].where(ou_ok), f"{book}<2.5": raw["Odds Under"].where(ou_ok),
    })


def join_sources(sharp: pd.DataFrame, soft: pd.DataFrame, team_map: dict[str, str] | None = None
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach soft-book columns to sharp rows by date + loose team names; returns (joined, unmatched soft rows).

    `team_map` maps a soft-book team name to the sharp source's name for teams spelled differently.
    """
    team_map = {team_key(k): team_key(v) for k, v in (team_map or {}).items()}
    fix = lambda n: team_map.get(team_key(n), team_key(n))  # noqa: E731
    soft = soft.copy()
    soft["date"] = parse_dates(soft["Date"].astype(str))
    soft["_h"], soft["_a"] = soft["HomeTeam"].map(fix), soft["AwayTeam"].map(fix)
    sharp = sharp.assign(_h=sharp["home"].map(team_key), _a=sharp["away"].map(team_key))
    extra = [c for c in soft.columns if c not in ("Date", "HomeTeam", "AwayTeam", "date", "_h", "_a")]
    joined = sharp.drop(columns=[c for c in extra if c in sharp.columns]).merge(
        soft[["date", "_h", "_a"] + extra], on=["date", "_h", "_a"], how="left", indicator=True)
    matched = joined.loc[joined["_merge"] == "both", ["date", "_h", "_a"]]
    unmatched = soft.merge(matched, on=["date", "_h", "_a"], how="left", indicator=True)
    unmatched = unmatched.loc[unmatched["_merge"] == "left_only", ["Date", "HomeTeam", "AwayTeam"]]
    return joined.drop(columns=["_h", "_a", "_merge"]), unmatched.reset_index(drop=True)


def find_value(df: pd.DataFrame, books: list[str], staking: StakingConfig, ev_min: float = 0.02,
               max_odds: float = 8.0, devig_method: str = "power",
               markets: tuple[str, ...] = ("1x2", "ou25")) -> pd.DataFrame:
    """Best soft price per selection vs the de-vigged sharp price; keeps selections with EV >= ev_min."""
    out = []
    for market in markets:
        spec = MARKETS[market]
        sharp = np.column_stack([_odds(df, c) for c in spec["sharp"]])
        fair = devig(sharp, devig_method)
        for k, (suffix, label) in enumerate(zip(spec["suffixes"], spec["labels"])):
            prices = np.column_stack([_odds(df, f"{b}{suffix}") for b in books])
            has = np.isfinite(prices).any(axis=1)
            best = np.full(len(df), -1)
            best[has] = np.nanargmax(prices[has], axis=1)
            odds = np.where(has, prices[np.arange(len(df)), np.clip(best, 0, None)], np.nan)
            ev = odds * fair[:, k] - 1
            take = np.isfinite(ev) & (ev >= ev_min) & (odds <= max_odds)
            if not take.any():
                continue
            rows = df.loc[take, KEY].copy()
            rows["market"], rows["selection"] = market, label
            rows["book"] = np.array(books)[best[take]]
            rows["odds"], rows["fair_p"] = odds[take], fair[take, k]
            rows["fair_odds"], rows["ev"] = 1 / fair[take, k], ev[take]
            rows["stake"] = stake_size(fair[take, k], odds[take], staking.bankroll, staking)
            out.append(rows)
    if not out:
        return pd.DataFrame(columns=LOG_COLUMNS)
    return pd.concat(out, ignore_index=True).sort_values(["date", "home", "market"], kind="stable")[LOG_COLUMNS]


def read_log(path: str | Path) -> pd.DataFrame:
    """Read a bet log; selections such as "1" and "2" stay text."""
    return pd.read_csv(path, parse_dates=["date"], dtype={"selection": str, "book": str})


def append_log(bets: pd.DataFrame, log_path: str | Path) -> pd.DataFrame:
    """Add new bets to the log; a selection already logged keeps its first (earliest) price."""
    log_path = Path(log_path)
    if log_path.exists():
        old = read_log(log_path)
        bets = pd.concat([old, bets], ignore_index=True)
    bets = bets.drop_duplicates(subset=KEY + ["market", "selection"], keep="first")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    bets.to_csv(log_path, index=False)
    return bets


def settle(log: pd.DataFrame, results: pd.DataFrame, devig_method: str = "power") -> pd.DataFrame:
    """Add result, profit and closing-line metrics (CLV, closing EV) from a results file with PSC* columns."""
    res = results.assign(_h=results["home"].map(team_key), _a=results["away"].map(team_key))
    log = log.assign(_h=log["home"].map(team_key), _a=log["away"].map(team_key))
    cols = ["date", "_h", "_a", "FTHG", "FTAG"] + [c for m in MARKETS.values() for c in m["close"] if c in res]
    merged = log.merge(res[cols].drop_duplicates(["date", "_h", "_a"]), on=["date", "_h", "_a"], how="left")
    hg = pd.to_numeric(merged["FTHG"], errors="coerce").to_numpy()
    ag = pd.to_numeric(merged["FTAG"], errors="coerce").to_numpy()
    won = np.full(len(merged), np.nan)
    close_odds = np.full(len(merged), np.nan)
    close_fair = np.full(len(merged), np.nan)
    for market, spec in MARKETS.items():
        rows = (merged["market"] == market).to_numpy().copy()
        winner = (np.where(hg > ag, 0, np.where(hg == ag, 1, 2)) if market == "1x2"
                  else np.where(hg + ag > 2.5, 0, 1))
        k = merged["selection"].astype(str).map({lab: i for i, lab in enumerate(spec["labels"])}).to_numpy()
        rows &= np.isfinite(k.astype(float))
        closing = np.column_stack([_odds(merged, c) for c in spec["close"]])
        fair = devig(closing, devig_method)
        idx = np.where(rows)[0]
        if len(idx) == 0:
            continue
        kk = k[idx].astype(int)
        won[idx] = np.where(np.isfinite(hg[idx]), winner[idx] == kk, np.nan)
        close_odds[idx] = closing[idx, kk]
        close_fair[idx] = fair[idx, kk]
    out = merged.drop(columns=["_h", "_a"])
    out["won"] = won
    out["profit"] = np.where(np.isnan(won), np.nan, np.where(won == 1, out["stake"] * (out["odds"] - 1), -out["stake"]))
    out["profit_flat"] = np.where(np.isnan(won), np.nan, np.where(won == 1, out["odds"] - 1, -1.0))
    out["close_odds"] = close_odds
    out["clv"] = out["odds"] / close_odds - 1
    out["ev_close"] = out["odds"] * close_fair - 1
    return out


def _mean_se(x: pd.Series) -> tuple[float | None, float | None]:
    x = x.dropna()
    if len(x) == 0:
        return None, None
    return float(x.mean()), float(x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else None


def summarize(settled: pd.DataFrame) -> dict:
    """Closing EV is the main edge estimate; realized yield needs thousands of bets to be informative."""
    done = settled[settled["won"].notna()]
    ev_close, ev_se = _mean_se(settled["ev_close"])
    flat, flat_se = _mean_se(done["profit_flat"])
    staked = float(done["stake"].sum())
    return {
        "bets": int(len(settled)), "settled": int(len(done)),
        "with_closing_odds": int(settled["ev_close"].notna().sum()),
        "ev_close_mean": ev_close, "ev_close_se": ev_se,
        "clv_mean": _mean_se(settled["clv"])[0],
        "clv_positive_share": float((settled["clv"].dropna() > 0).mean()) if settled["clv"].notna().any() else None,
        "hit_rate": float(done["won"].mean()) if len(done) else None,
        "yield_flat": flat, "yield_flat_se": flat_se,
        "staked": staked, "profit": float(done["profit"].sum()),
        "yield_kelly": float(done["profit"].sum() / staked) if staked else None,
    }


def value_backtest(data: str | Path, books: list[str], start: str | None, staking: StakingConfig,
                   ev_min: float = 0.02, max_odds: float = 8.0, devig_method: str = "power") -> pd.DataFrame:
    """Historical test on football-data files: bets picked from early odds, judged at Pinnacle closing."""
    df = load_odds_table(data)
    df = df.dropna(subset=["FTHG", "FTAG"])
    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    df = df.reset_index(drop=True)
    bets = find_value(df, books, staking, ev_min, max_odds, devig_method)
    return settle(bets, df, devig_method)
