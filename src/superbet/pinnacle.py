"""Pinnacle prematch odds via pinnapi.com, in the football-data layout used by `superbet value`.

The API key is read from the PINNAPI_KEY environment variable and never written anywhere.
Each snapshot can be appended to a history file; the last snapshot taken before
kick-off is the (approximate) closing line used by `superbet value-settle`.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

BASE_URL = "https://pinnapi.com/kit/v1"
SOCCER = 1
LOCAL_TZ = "Europe/Bucharest"
SHARP_COLUMNS = ["PSH", "PSD", "PSA", "P>2.5", "P<2.5"]
CLOSING_COLUMNS = ["PSCH", "PSCD", "PSCA", "PC>2.5", "PC<2.5"]


def api_key() -> str:
    """PINNAPI_KEY from the environment (a GitHub/environment secret, never a file in the repo)."""
    key = os.environ.get("PINNAPI_KEY", "").strip()
    if not key:
        raise RuntimeError("PINNAPI_KEY is not set")
    return key


def fetch_prematch(key: str, sport_id: int = SOCCER, timeout: int = 60) -> dict:
    """One REST call: every prematch event for the sport (counts as 1 of the free tier's 100/day)."""
    req = urllib.request.Request(f"{BASE_URL}/markets?sport_id={sport_id}&event_type=prematch",
                                 headers={"x-portal-apikey": key, "User-Agent": "superbet-value/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _is_main_event(ev: dict) -> bool:
    """Skip corners/bookings pseudo-events and other child events."""
    text = f"{ev.get('home', '')} {ev.get('away', '')} {ev.get('league_name', '')}"
    return not ev.get("parent_id") and "Corners" not in text and "Bookings" not in text


def events_to_frame(payload: dict, taken_at: pd.Timestamp) -> pd.DataFrame:
    """Full-match 1X2 and Over/Under 2.5 per event; Date is the local (Bucharest) match day."""
    rows = []
    for ev in payload.get("events", []):
        game = (ev.get("periods") or {}).get("num_0") or {}
        if not _is_main_event(ev) or game.get("status", "open") != "open":
            continue
        ml = game.get("money_line") or {}
        tot = (game.get("totals") or {}).get("2.5") or {}
        rows.append({"event_id": ev["event_id"], "starts": ev["starts"], "League": ev.get("league_name"),
                     "HomeTeam": ev["home"], "AwayTeam": ev["away"],
                     "PSH": ml.get("home"), "PSD": ml.get("draw"), "PSA": ml.get("away"),
                     "P>2.5": tot.get("over"), "P<2.5": tot.get("under")})
    df = pd.DataFrame(rows, columns=["event_id", "starts", "League", "HomeTeam", "AwayTeam"] + SHARP_COLUMNS)
    df["starts"] = pd.to_datetime(df["starts"], utc=True)
    df = df[df["starts"] > taken_at].copy()
    df.insert(0, "taken_at", taken_at)
    df.insert(1, "Date", df["starts"].dt.tz_convert(LOCAL_TZ).dt.strftime("%d/%m/%Y"))
    return df.dropna(subset=["PSH", "PSD", "PSA"], how="all").reset_index(drop=True)


def compact_history(history: str | Path, keep_days: int = 45) -> pd.DataFrame:
    """Keep, per event, only the latest snapshot taken before kick-off, and drop events older than `keep_days`.

    That is all `closing_lines` needs, so the history stays small enough to commit.
    """
    h = pd.read_csv(history)
    columns = list(h.columns)  # appends are positional, so the column order must not change
    taken = pd.to_datetime(h["taken_at"], utc=True, format="mixed")
    starts = pd.to_datetime(h["starts"], utc=True, format="mixed")
    cutoff = taken.max() - pd.Timedelta(days=keep_days)
    h = h[(taken < starts) & (starts >= cutoff)]
    h = h.assign(_t=taken).sort_values("_t").groupby("event_id", as_index=False).last()
    h = h.sort_values(["_t", "event_id"])[columns]
    h.to_csv(history, index=False)
    return h


def snapshot(out: str | Path, history: str | Path | None = None, days: int = 3,
             key: str | None = None, now: pd.Timestamp | None = None, payload: dict | None = None,
             compact: bool = True) -> pd.DataFrame:
    """Write the current sharp table (matches in the next `days`) and add it to `history` (compacted by default)."""
    now = now or pd.Timestamp.now(tz="UTC")
    payload = payload if payload is not None else fetch_prematch(key or api_key())
    df = events_to_frame(payload, now)
    df = df[df["starts"] <= now + pd.Timedelta(days=days)]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["taken_at"]).to_csv(out, index=False)
    if history is not None:
        history = Path(history)
        history.parent.mkdir(parents=True, exist_ok=True)
        if history.exists():  # align with the file's header, whatever order it was written in
            df = df[pd.read_csv(history, nrows=0).columns.tolist()]
        df.to_csv(history, mode="a", header=not history.exists(), index=False)
        if compact:
            compact_history(history)
    return df


def closing_lines(history: str | Path) -> pd.DataFrame:
    """Per event, the last snapshot taken before kick-off, as closing columns (PSCH.., PC>2.5..)."""
    h = pd.read_csv(history)
    h["taken_at"] = pd.to_datetime(h["taken_at"], utc=True, format="mixed")
    h["starts"] = pd.to_datetime(h["starts"], utc=True, format="mixed")
    h = h[h["taken_at"] < h["starts"]].sort_values("taken_at")
    last = h.groupby("event_id", as_index=False).last()
    last = last.rename(columns=dict(zip(SHARP_COLUMNS, CLOSING_COLUMNS)))
    last["minutes_before_kickoff"] = (last["starts"] - last["taken_at"]).dt.total_seconds() / 60
    cols = ["event_id", "Date", "League", "HomeTeam", "AwayTeam", "starts", "taken_at", "minutes_before_kickoff"]
    return last[cols + CLOSING_COLUMNS].reset_index(drop=True)


def attach_closing(results: pd.DataFrame | None, closing: pd.DataFrame) -> pd.DataFrame:
    """Results table for `settle`: closing columns from snapshots, plus FTHG/FTAG where a results file has them."""
    closing = closing.assign(date=pd.to_datetime(closing["Date"], format="%d/%m/%Y"),
                             home=closing["HomeTeam"], away=closing["AwayTeam"])
    if results is None:
        return closing.assign(FTHG=np.nan, FTAG=np.nan)
    from .value import team_key

    res = results.assign(_h=results["home"].map(team_key), _a=results["away"].map(team_key))
    res = res[["date", "_h", "_a", "FTHG", "FTAG"]].drop_duplicates(["date", "_h", "_a"])
    closing = closing.assign(_h=closing["home"].map(team_key), _a=closing["away"].map(team_key))
    return closing.merge(res, on=["date", "_h", "_a"], how="left").drop(columns=["_h", "_a"])
