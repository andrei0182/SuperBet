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
    record_day(state, date, compared, len(unmatched), len(bets))
    settled = settle_log(state)
    summary = summarize(settled) if settled is not None else {}
    return DailyResult(bets, compared, unmatched, summary)


def record_day(state: Path, date: str, compared: int, unmatched: int, bets: int) -> None:
    """One row per day in daily_stats.csv (a re-run of the same day replaces its row)."""
    path = state / "daily_stats.csv"
    row = pd.DataFrame([{"date": date, "compared": compared, "unmatched": unmatched, "bets": bets}])
    if path.exists():
        old = pd.read_csv(path, dtype={"date": str})
        row = pd.concat([old[old["date"] != date], row], ignore_index=True)
    row.sort_values("date").to_csv(path, index=False)


def settle_log(state: Path) -> pd.DataFrame | None:
    """Settle every logged bet on the closing lines from the snapshot history; None when nothing is logged."""
    log_path, history = state / "value_log.csv", state / "pinnacle_snapshots.csv"
    if not log_path.exists():
        return None
    closing = attach_closing(None, closing_lines(history)) if history.exists() else None
    if closing is None:
        return None
    settled = settle(read_log(log_path), closing)
    settled.to_csv(state / "value_settled.csv", index=False)
    return settled


def weekly_html(state_dir: str | Path, end: str) -> tuple[str, str]:
    """Subject and HTML for the 7 days before `end` (exclusive), plus the all-time closing-line record."""
    state = Path(state_dir)
    end_ts = pd.Timestamp(end)
    start_ts = end_ts - pd.Timedelta(days=7)
    period = f"{start_ts:%d.%m} &ndash; {end_ts - pd.Timedelta(days=1):%d.%m.%Y}"
    e = html.escape

    stats_path = state / "daily_stats.csv"
    days = pd.read_csv(stats_path, parse_dates=["date"]) if stats_path.exists() else pd.DataFrame(
        columns=["date", "compared", "unmatched", "bets"])
    week_days = days[(days["date"] >= start_ts) & (days["date"] < end_ts)]
    settled = settle_log(state)
    week = (settled[(settled["date"] >= start_ts) & (settled["date"] < end_ts)]
            if settled is not None else pd.DataFrame())

    parts = [f"<h2>Rezumat săptămânal value bets &mdash; {period}</h2>",
             f"<p>Zile rulate: {len(week_days)} din 7 &middot; meciuri comparate: {int(week_days['compared'].sum())} "
             f"&middot; pariuri găsite: {len(week)}.</p>"]
    if not week.empty:
        rows = "".join(
            f"<tr><td>{r.date:%d.%m}</td><td>{e(r.home)} &ndash; {e(r.away)}</td><td>{e(str(r.selection))}</td>"
            f"<td>{r.odds:.2f}</td><td>{r.ev * 100:+.1f}%</td>"
            f"<td>{'-' if pd.isna(r.close_odds) else f'{r.close_odds:.2f}'}</td>"
            f"<td>{'-' if pd.isna(r.ev_close) else f'{r.ev_close * 100:+.1f}%'}</td></tr>"
            for r in week.itertuples())
        parts.append("<table border='1' cellpadding='4' cellspacing='0'><tr><th>Data</th><th>Meci</th><th>Pariu</th>"
                     "<th>Cota luată</th><th>EV la pariere</th><th>Cota Pinnacle la închidere</th>"
                     f"<th>EV la închidere</th></tr>{rows}</table>")
        ws = summarize(week)
        parts.append(f"<p>Săptămâna: EV la închidere {_pct(ws['ev_close_mean'])} &plusmn; {_pct(ws['ev_close_se'])}, "
                     f"pariuri cu CLV pozitiv {_pct(ws['clv_positive_share'])}.</p>")
    if settled is not None and len(settled):
        s = summarize(settled)
        verdict = ("clar pozitiv" if s["ev_close_se"] and s["ev_close_mean"] / s["ev_close_se"] > 2
                   else "încă neconcludent (prea puține pariuri sau fără avantaj)")
        parts.append(f"<h3>De la început: {s['bets']} pariuri ({s['with_closing_odds']} cu linie de închidere)</h3>"
                     f"<p>EV la închidere: <b>{_pct(s['ev_close_mean'])}</b> &plusmn; {_pct(s['ev_close_se'])} "
                     f"&mdash; {verdict}. Pariuri cu CLV pozitiv: {_pct(s['clv_positive_share'])}.</p>")
    else:
        parts.append("<p>Încă niciun pariu în jurnal.</p>")
    parts.append("<p style='color:#666'>Profitul pe o săptămână e aproape numai zgomot; contează EV-ul la închidere "
                 "pe sute de pariuri. Pariază doar sume pe care îți permiți să le pierzi.</p>")
    subject = f"Rezumat saptamanal value bets ({len(week)} pariuri) -- {start_ts:%d.%m}-{end_ts - pd.Timedelta(days=1):%d.%m.%Y}"
    return subject, "\n".join(parts)


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
