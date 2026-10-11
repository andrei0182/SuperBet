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
LOG_COLUMNS = KEY + ["market", "selection", "book", "odds", "fair_p", "fair_odds", "ev", "stake", "league", "kickoff"]
LOCAL_TZ = "Europe/Bucharest"


def _kickoff_local(df: pd.DataFrame) -> pd.Series:
    """Local (Bucharest) HH:MM from the sharp table's UTC `starts`, empty when unknown."""
    if "starts" not in df.columns:
        return pd.Series("", index=df.index)
    starts = pd.to_datetime(df["starts"], utc=True, errors="coerce", format="mixed")
    return starts.dt.tz_convert(LOCAL_TZ).dt.strftime("%H:%M").fillna("")


# Romanian spellings used by Superbet for national teams -> English (as in Pinnacle / football-data).
RO_COUNTRIES = {
    "japonia": "japan", "coreea de sud": "south korea", "coreea de nord": "north korea", "china": "china",
    "noua zeelanda": "new zealand", "insulele maldive": "maldives", "insulele solomon": "solomon islands",
    "insulele comore": "comoros", "insulele feroe": "faroe islands", "coasta de fildes": "ivory coast",
    "camerun": "cameroon", "libia": "libya", "rd congo": "dr congo", "congo": "congo republic",
    "guineea ecuatoriala": "equatorial guinea", "guineea": "guinea", "guineea bissau": "guinea bissau",
    "republica centrafricana": "central african republic", "africa de sud": "south africa",
    "eau": "united arab emirates", "emiratele arabe unite": "united arab emirates", "arabia saudita": "saudi arabia",
    "iordania": "jordan", "siria": "syria", "irak": "iraq", "liban": "lebanon", "kuweit": "kuwait",
    "tunisia": "tunisia", "algeria": "algeria", "maroc": "morocco", "egipt": "egypt", "etiopia": "ethiopia",
    "olanda": "netherlands", "tarile de jos": "netherlands", "germania": "germany", "tara galilor": "wales",
    "anglia": "england", "scotia": "scotland", "irlanda": "ireland", "irlanda de nord": "northern ireland",
    "grecia": "greece", "danemarca": "denmark", "norvegia": "norway", "suedia": "sweden", "finlanda": "finland",
    "islanda": "iceland", "lituania": "lithuania", "letonia": "latvia", "ungaria": "hungary", "belgia": "belgium",
    "portugalia": "portugal", "spania": "spain", "franta": "france", "italia": "italy", "elvetia": "switzerland",
    "polonia": "poland", "cehia": "czechia", "slovacia": "slovakia", "croatia": "croatia", "slovenia": "slovenia",
    "bosnia si hertegovina": "bosnia and herzegovina", "muntenegru": "montenegro", "macedonia de nord":
    "north macedonia", "albania": "albania", "turcia": "turkiye", "cipru": "cyprus", "moldova": "moldova",
    "ucraina": "ukraine", "rusia": "russia", "belarus": "belarus", "georgia": "georgia", "armenia": "armenia",
    "azerbaidjan": "azerbaijan", "kazahstan": "kazakhstan", "gibraltar": "gibraltar", "luxemburg": "luxembourg",
    "statele unite": "usa", "sua": "usa", "mexic": "mexico", "canada": "canada", "brazilia": "brazil",
    "columbia": "colombia", "peru": "peru", "antigua si barbuda": "antigua and barbuda",
    "trinidad si tobago": "trinidad and tobago", "australia": "australia", "india": "india",
}


def team_key(name: object) -> str:
    """Loose team key for joining sources: no accents, case, punctuation or club suffixes; RO country names in English."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = RO_COUNTRIES.get(text, text)
    text = re.sub(r"\b(fc|cf|afc|sc|ac|cd|fk|sk|as|ss|us|the)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(a: str, b: str) -> float:
    """Name similarity in [0, 1]: sequence ratio, or 1 when one name's words are all inside the other."""
    from difflib import SequenceMatcher

    wa, wb = set(a.split()), set(b.split())
    if wa and wb and (wa <= wb or wb <= wa):
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _fuzzy_pairs(sharp: pd.DataFrame, soft: pd.DataFrame, threshold: float) -> dict[int, int]:
    """Soft row -> sharp row for same-day fixtures whose home and away names are both similar enough."""
    pairs, used = {}, set()
    for i, row in soft.iterrows():
        cand = sharp[sharp["date"] == row["date"]]
        best, best_score = None, threshold
        for j, c in cand.iterrows():
            if j in used:
                continue
            score = min(_similarity(row["_h"], c["_h"]), _similarity(row["_a"], c["_a"]))
            if score >= best_score:
                best, best_score = j, score
        if best is not None:
            pairs[i] = best
            used.add(best)
    return pairs


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


def join_sources(sharp: pd.DataFrame, soft: pd.DataFrame, team_map: dict[str, str] | None = None,
                 book: str = "SB", fuzzy: float = 0.75, max_gap: float = 0.15
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach soft-book columns to sharp rows by date + team names; returns (joined, unmatched soft rows).

    Names are matched exactly (after `team_key`), then fuzzily on the same day. A pair is rejected
    when the two books' de-vigged 1X2 probabilities differ by more than `max_gap` (likely a wrong match).
    `team_map` maps a soft-book team name to the sharp source's name for teams spelled differently.
    """
    team_map = {team_key(k): team_key(v) for k, v in (team_map or {}).items()}
    fix = lambda n: team_map.get(team_key(n), team_key(n))  # noqa: E731
    soft = soft.copy().reset_index(drop=True)
    soft["date"] = parse_dates(soft["Date"].astype(str))
    soft["_h"], soft["_a"] = soft["HomeTeam"].map(fix), soft["AwayTeam"].map(fix)
    sharp = sharp.reset_index(drop=True).assign(_h=sharp["home"].map(team_key).to_numpy(),
                                                _a=sharp["away"].map(team_key).to_numpy())
    extra = [c for c in soft.columns if c not in ("Date", "HomeTeam", "AwayTeam", "date", "_h", "_a")]

    exact = soft.reset_index().merge(sharp[["date", "_h", "_a"]].reset_index().rename(columns={"index": "j"}),
                                     on=["date", "_h", "_a"])
    pairs = dict(zip(exact["index"], exact["j"]))
    rest = soft.drop(index=list(pairs))
    pairs.update(_fuzzy_pairs(sharp.drop(index=list(pairs.values())), rest, fuzzy))

    pairs = {i: j for i, j in pairs.items() if _prices_agree(sharp.loc[j], soft.loc[i], book, max_gap)}
    joined = sharp.drop(columns=[c for c in extra if c in sharp.columns]).copy()
    for c in extra:
        joined[c] = np.nan
    for i, j in pairs.items():
        joined.loc[j, extra] = soft.loc[i, extra].to_numpy()
    joined["matched_as"] = pd.Series({j: f"{soft.loc[i, 'HomeTeam']} - {soft.loc[i, 'AwayTeam']}" for i, j in pairs.items()})
    unmatched = soft.drop(index=list(pairs))[["Date", "HomeTeam", "AwayTeam"]]
    return joined.drop(columns=["_h", "_a"]), unmatched.reset_index(drop=True)


def _prices_agree(sharp_row: pd.Series, soft_row: pd.Series, book: str, max_gap: float) -> bool:
    """False when both books quote 1X2 and their fair probabilities are too far apart to be the same match."""
    s = np.array([sharp_row.get(c, np.nan) for c in MARKETS["1x2"]["sharp"]], dtype=float)
    o = np.array([soft_row.get(f"{book}{x}", np.nan) for x in MARKETS["1x2"]["suffixes"]], dtype=float)
    if not (np.isfinite(s).all() and np.isfinite(o).all()):
        return True
    return float(np.max(np.abs(devig(s[None], "proportional") - devig(o[None], "proportional")))) <= max_gap


def find_value(df: pd.DataFrame, books: list[str], staking: StakingConfig, ev_min: float = 0.02,
               max_odds: float = 8.0, devig_method: str = "power",
               markets: tuple[str, ...] = ("1x2", "ou25")) -> pd.DataFrame:
    """Best soft price per selection vs the de-vigged sharp price; keeps selections with EV >= ev_min."""
    out = []
    kickoff = _kickoff_local(df).to_numpy()
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
            rows["league"] = df.loc[take, "League"].fillna("") if "League" in df.columns else ""
            rows["kickoff"] = kickoff[take]
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


def settle(log: pd.DataFrame, results: pd.DataFrame | None, devig_method: str = "power") -> pd.DataFrame:
    """Add result, profit and closing-line metrics from a results table (FTHG/FTAG and/or PSC* closing columns).

    Scores already on the log (e.g. from `results.attach_scores`) take precedence over the results table's.
    The edge estimate is `ev_close` = price taken x Pinnacle's no-vig (power) closing probability - 1,
    and only for bets whose closing line is a capture distinct from the one the bet was found in
    (`closing_status == "ok"`). `clv_raw` compares raw prices, vig included: informational only.
    """
    if results is None:
        results = pd.DataFrame(columns=["date", "home", "away"])
    res = results.assign(_h=results["home"].map(team_key), _a=results["away"].map(team_key))
    log = log.assign(_h=log["home"].map(team_key), _a=log["away"].map(team_key))
    own_scores = {c: log.pop(c) for c in ("FTHG", "FTAG") if c in log}
    res = res.assign(**{c: np.nan for c in ("FTHG", "FTAG") if c not in res})
    if "taken_at" in res:  # closing lines from the snapshot history carry the time they were taken
        res = res.rename(columns={"taken_at": "close_taken_at"})
    cols = ["date", "_h", "_a", "FTHG", "FTAG"] + [c for m in MARKETS.values() for c in m["close"] if c in res]
    cols += [c for c in ("close_taken_at", "minutes_before_kickoff") if c in res]
    merged = log.merge(res[cols].drop_duplicates(["date", "_h", "_a"]), on=["date", "_h", "_a"], how="left")
    for c, own in own_scores.items():  # left merge keeps the log's row order
        own = pd.to_numeric(own, errors="coerce").to_numpy(dtype=float)
        merged[c] = np.where(np.isfinite(own), own, pd.to_numeric(merged[c], errors="coerce"))
    hg = pd.to_numeric(merged["FTHG"], errors="coerce").to_numpy(dtype=float)
    ag = pd.to_numeric(merged["FTAG"], errors="coerce").to_numpy(dtype=float)
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
        won[idx] = np.where(np.isfinite(hg[idx]) & np.isfinite(ag[idx]), winner[idx] == kk, np.nan)
        close_odds[idx] = closing[idx, kk]
        close_fair[idx] = fair[idx, kk]
    out = merged.drop(columns=["_h", "_a"])
    out["won"] = won
    out["result_status"] = np.where(np.isnan(won), "pending", "settled")
    out["profit"] = np.where(np.isnan(won), np.nan, np.where(won == 1, out["stake"] * (out["odds"] - 1), -out["stake"]))
    out["profit_flat"] = np.where(np.isnan(won), np.nan, np.where(won == 1, out["odds"] - 1, -1.0))
    ev_close = out["odds"].to_numpy(dtype=float) * close_fair - 1
    status = closing_status(out, close_odds, ev_close)
    valid = status == "ok"
    out["closing_status"] = status
    out["close_odds"] = np.where(valid, close_odds, np.nan)
    out["close_fair_p"] = np.where(valid, close_fair, np.nan)
    out["ev_close"] = np.where(valid, ev_close, np.nan)
    out["clv_raw"] = out["odds"] / out["close_odds"] - 1
    if "minutes_before_kickoff" in out:
        out["close_minutes_before"] = np.where(valid, out.pop("minutes_before_kickoff"), np.nan)
    return out.drop(columns=[c for c in ("clv",) if c in out])


MIN_CLOSING_GAP = pd.Timedelta(minutes=30)
SAME_CAPTURE_TOL = 1e-9
CLOSING_STATUSES = {
    "ok": "închidere distinctă de captura în care s-a găsit pariul",
    "no_close": "ultima captură dinainte de start e chiar captura de selecție (sau prea apropiată de ea)",
    "missing_close": "nicio cotă Pinnacle de închidere pentru meci",
}


def closing_status(settled: pd.DataFrame, close_odds: np.ndarray, ev_close: np.ndarray | None = None) -> np.ndarray:
    """Whether each bet's closing line is a real closing line, i.e. a later capture than the bet's own.

    "ok": a closing price exists and was taken at least 30 minutes after the bet (`logged_at`);
    "no_close": the last pre-start capture is the selection capture (or within 30 minutes of it);
    "missing_close": no closing price at all.
    Bets logged before `logged_at` existed are judged by price: when the "closing" capture yields exactly
    the bet's own EV, it is the selection capture (`no_close`); a different price can only come from a later
    capture, because the history keeps the latest snapshot before kick-off.
    Historical result files (no snapshot times) count as "ok".
    """
    has_close = np.isfinite(close_odds)
    status = np.where(has_close, "ok", "missing_close").astype(object)
    if "close_taken_at" not in settled:
        return status
    taken = pd.to_datetime(settled["close_taken_at"], utc=True, errors="coerce", format="mixed")
    logged = pd.Series(pd.NaT, index=settled.index, dtype="datetime64[ns, UTC]")
    if "logged_at" in settled:
        logged = pd.to_datetime(settled["logged_at"], utc=True, errors="coerce", format="mixed")
    unknown = logged.isna().to_numpy()
    too_early = np.zeros(len(settled), dtype=bool)
    known = has_close & ~unknown
    too_early[known] = (taken[known] < logged[known] + MIN_CLOSING_GAP).to_numpy()
    if ev_close is not None and "ev" in settled:
        same_price = np.abs(settled["ev"].to_numpy(dtype=float) - ev_close) < SAME_CAPTURE_TOL
        too_early |= has_close & unknown & same_price
    status[too_early] = "no_close"
    return status


def _mean_se(x: pd.Series) -> tuple[float | None, float | None]:
    x = x.dropna()
    if len(x) == 0:
        return None, None
    return float(x.mean()), float(x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else None


def summarize(settled: pd.DataFrame) -> dict:
    """Closing EV (de-vigged, distinct closing captures only) is the edge estimate; bets without one are counted apart.

    Hit rate and yield are computed on the same measured bets; realized yield needs thousands of bets.
    """
    status = settled["closing_status"] if "closing_status" in settled else pd.Series("ok", index=settled.index)
    measured = settled[status == "ok"]
    done = measured[measured["won"].notna()]
    ev = measured["ev_close"]
    ev_close, ev_se = _mean_se(ev)
    flat, flat_se = _mean_se(done["profit_flat"])
    staked = float(done["stake"].sum())
    return {
        "bets": int(len(settled)),
        "with_closing_odds": int(ev.notna().sum()),
        "no_close": int((status == "no_close").sum()),
        "missing_close": int((status == "missing_close").sum()),
        "settled": int(len(done)),
        "pending_results": int(settled["won"].isna().sum()),
        "ev_close_mean": ev_close, "ev_close_se": ev_se,
        "ev_close_positive_share": float((ev.dropna() > 0).mean()) if ev.notna().any() else None,
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
