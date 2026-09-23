"""Closing-line check for the history-based "100% Over 2.5" recommendations.

Each logged pick (Superbet Over 2.5 price) is paired with the same match in the Pinnacle snapshot
history; the last snapshot before kick-off is the closing line. EV at close = Superbet price x
Pinnacle's no-vig closing probability - 1: positive on average means the picks beat the market.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .blend import devig
from .value import _similarity, team_key


def over_closing(recs: pd.DataFrame, closing: pd.DataFrame, fuzzy: float = 0.75, max_gap: float = 0.15) -> pd.DataFrame:
    """Add close_over, fair_over, clv and ev_close to each recommendation that has a Pinnacle closing line.

    `recs` needs date (YYYY-MM-DD), home_team, away_team, odds_over; `closing` is pinnacle.closing_lines().
    A pairing is rejected when Superbet's implied Over probability and Pinnacle's differ by more than `max_gap`.
    """
    close = closing.assign(_d=pd.to_datetime(closing["Date"], format="%d/%m/%Y"),
                           _h=closing["HomeTeam"].map(team_key), _a=closing["AwayTeam"].map(team_key))
    close = close[close["PC>2.5"].notna() & close["PC<2.5"].notna()]
    fair = devig(close[["PC>2.5", "PC<2.5"]].to_numpy(dtype=float), "power")[:, 0] if len(close) else np.empty(0)
    close = close.assign(_fair=fair)
    out = recs.copy()
    for col in ("close_over", "fair_over", "clv", "ev_close"):
        out[col] = np.nan
    for i, rec in recs.iterrows():
        odds = pd.to_numeric(rec.get("odds_over"), errors="coerce")
        if not np.isfinite(odds):
            continue
        h, a = team_key(rec["home_team"]), team_key(rec["away_team"])
        cand = close[close["_d"] == pd.Timestamp(rec["date"])]
        best, best_score = None, fuzzy
        for j, c in cand.iterrows():
            score = min(_similarity(h, c["_h"]), _similarity(a, c["_a"]))
            if score >= best_score:
                best, best_score = j, score
        if best is None or abs(1 / odds - close.loc[best, "_fair"]) > max_gap:
            continue
        c = close.loc[best]
        out.loc[i, ["close_over", "fair_over"]] = [c["PC>2.5"], c["_fair"]]
        out.loc[i, "clv"] = odds / c["PC>2.5"] - 1
        out.loc[i, "ev_close"] = odds * c["_fair"] - 1
    return out


def over_closing_html(checked: pd.DataFrame) -> str:
    """Email section: how past picks compare with Pinnacle's closing Over 2.5 line."""
    done = checked[checked["ev_close"].notna()]
    if done.empty:
        return ("<p style='color:#555;'><b>Linia de închidere Pinnacle:</b> încă nicio recomandare anterioară "
                "cu linie de închidere (apar după ce meciurile recomandate încep).</p>")
    ev = done["ev_close"]
    se = ev.std(ddof=1) / np.sqrt(len(ev)) if len(ev) > 1 else None
    verdict = ("recomandările bat piața" if se and ev.mean() / se > 2 else
               "încă neconcludent" if not se or ev.mean() / se > -2 else "recomandările NU bat piața")
    se_txt = f" &plusmn; {se * 100:.1f}%" if se else ""
    last = done.sort_values("date").tail(5)
    rows = "".join(f"<tr><td>{r.date}</td><td>{r.home_team} &ndash; {r.away_team}</td><td>{float(r.odds_over):.2f}</td>"
                   f"<td>{r.close_over:.2f}</td><td>{r.ev_close * 100:+.1f}%</td></tr>" for r in last.itertuples())
    return (f"<div style='margin-top:20px; padding-top:10px; border-top:1px solid #ddd;'>"
            f"<p><b>Recomandările anterioare vs linia de închidere Pinnacle</b> ({len(done)} cu linie de închidere): "
            f"EV la închidere <b>{ev.mean() * 100:+.1f}%</b>{se_txt}, cota Superbet mai bună decât Pinnacle la "
            f"închidere în {(done['clv'] > 0).mean():.0%} din cazuri &mdash; {verdict}.</p>"
            "<table border='1' cellpadding='4' cellspacing='0'><tr><th>Data</th><th>Meci</th><th>Cota Superbet</th>"
            f"<th>Pinnacle la închidere</th><th>EV la închidere</th></tr>{rows}</table>"
            "<p style='color:#888; font-size:0.9em;'>EV la închidere pozitiv, constant, pe sute de recomandări "
            "e cel mai credibil semn că istoricul chiar găsește meciuri subevaluate.</p></div>")
