"""Daily value-betting run: Pinnacle snapshot + Superbet odds -> value bets, bet log and CLV summary."""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .pinnacle import attach_closing, closing_lines, snapshot
from .staking import StakingConfig
from .value import append_log, find_value, join_sources, load_odds_table, load_superbet_excel, read_log, settle, \
    summarize


@dataclass
class DailyResult:
    """What one daily run produced."""

    bets: pd.DataFrame
    compared: int
    unmatched: pd.DataFrame
    summary: dict


def run_daily(date: str, superbet_xlsx: str | Path, state_dir: str | Path, staking: StakingConfig,
              ev_min: float = 0.02, max_odds: float = 8.0, team_map: dict[str, str] | None = None,
              payload: dict | None = None) -> DailyResult:
    """Snapshot Pinnacle, compare with Superbet for `date`, log new value bets, settle the log on closing lines."""
    state = Path(state_dir)
    sharp_path, history, log_path = state / "sharp.csv", state / "pinnacle_snapshots.csv", state / "value_log.csv"
    snapshot(sharp_path, history, payload=payload)
    joined, unmatched = join_sources(load_odds_table(sharp_path), load_superbet_excel(superbet_xlsx, date), team_map)
    joined = joined[joined["date"] == pd.Timestamp(date)]
    compared = int(joined["SBH"].notna().sum())
    bets = find_value(joined, ["SB"], staking, ev_min, max_odds)
    if not bets.empty:
        append_log(bets, log_path)
    summary = {}
    if log_path.exists():
        settled = settle(read_log(log_path), attach_closing(None, closing_lines(history)))
        settled.to_csv(state / "value_settled.csv", index=False)
        summary = summarize(settled)
    return DailyResult(bets, compared, unmatched, summary)


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def email_html(result: DailyResult, date: str) -> str:
    """Short HTML report: today's value bets and the running closing-line record."""
    e = html.escape
    parts = [f"<h2>Value bets Superbet vs Pinnacle &mdash; {e(date)}</h2>",
             f"<p>Meciuri comparate: {result.compared} (nepotrivite pe Pinnacle: {len(result.unmatched)}).</p>"]
    if result.bets.empty:
        parts.append("<p><b>Niciun pariu cu valoare azi.</b></p>")
    else:
        rows = "".join(
            f"<tr><td>{e(r.home)} &ndash; {e(r.away)}</td><td>{e(r.selection)}</td><td>{r.odds:.2f}</td>"
            f"<td>{r.fair_odds:.2f}</td><td>{r.ev * 100:+.1f}%</td><td>{r.stake:.2f}</td></tr>"
            for r in result.bets.itertuples())
        parts.append("<table border='1' cellpadding='4' cellspacing='0'><tr><th>Meci</th><th>Pariu</th>"
                     "<th>Cota Superbet</th><th>Cota corectă (Pinnacle)</th><th>EV</th><th>Miză sugerată</th></tr>"
                     f"{rows}</table>")
    s = result.summary
    if s and s.get("with_closing_odds"):
        verdict = ("pozitiv: prețurile luate bat linia de închidere" if s["ev_close_se"] and
                   s["ev_close_mean"] / s["ev_close_se"] > 2 else "încă neconcludent")
        parts.append(f"<h3>Bilanț (toate pariurile cu linie de închidere: {s['with_closing_odds']})</h3>"
                     f"<p>EV la închidere: <b>{_pct(s['ev_close_mean'])}</b> &plusmn; {_pct(s['ev_close_se'])} "
                     f"({verdict}). Pariuri cu CLV pozitiv: {_pct(s['clv_positive_share'])}.</p>")
    parts.append("<p style='color:#666'>Estimare, nu garanție. EV la închidere pozitiv, constant, pe sute de pariuri "
                 "e singurul semn credibil de avantaj. Pariază doar sume pe care îți permiți să le pierzi.</p>")
    return "\n".join(parts)
