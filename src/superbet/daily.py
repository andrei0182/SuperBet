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
    return run_range([date], {date: superbet_xlsx}, state_dir, staking, ev_min, max_odds, team_map, payload)[date]


def run_range(dates: list[str], superbet_xlsx: dict[str, str | Path], state_dir: str | Path, staking: StakingConfig,
              ev_min: float = 0.02, max_odds: float = 8.0, team_map: dict[str, str] | None = None,
              payload: dict | None = None) -> dict[str, DailyResult]:
    """One Pinnacle snapshot covering every date, then the daily comparison for each date (one Superbet file each)."""
    state = Path(state_dir)
    sharp_path = state / "sharp.csv"
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    days = max(3, (pd.Timestamp(max(dates)) - today).days + 2)
    snapshot(sharp_path, state / "pinnacle_snapshots.csv", days=days, payload=payload)
    sharp = load_odds_table(sharp_path)
    out = {d: _compare_day(d, sharp, superbet_xlsx[d], state, staking, ev_min, max_odds, team_map) for d in dates}
    settled = settle_log(state)
    summary = summarize(settled) if settled is not None else {}
    for res in out.values():
        res.summary = summary
    return out


def _compare_day(date: str, sharp: pd.DataFrame, superbet_xlsx: str | Path, state: Path, staking: StakingConfig,
                 ev_min: float, max_odds: float, team_map: dict[str, str] | None) -> DailyResult:
    """Superbet vs Pinnacle for one date: log value bets and record the day's counts."""
    joined, unmatched = join_sources(sharp, load_superbet_excel(superbet_xlsx, date), team_map)
    joined = joined[joined["date"] == pd.Timestamp(date)]
    compared = int(joined["SBH"].notna().sum())
    bets = find_value(joined, ["SB"], staking, ev_min, max_odds)
    if not bets.empty:
        append_log(bets, state / "value_log.csv")
    record_day(state, date, compared, len(unmatched), len(bets))
    return DailyResult(bets, compared, unmatched, {})


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


def bets_table(bets: pd.DataFrame, with_date: bool) -> str:
    """Value bets as an HTML table, in kick-off order, with the start time and league."""
    e = html.escape
    for col in ("kickoff", "league"):  # older log rows predate these columns
        bets = bets.assign(**{col: bets[col].fillna("") if col in bets else ""})
    head = (("<th>Data</th>" if with_date else "") + "<th>Ora</th><th>Liga</th><th>Meci</th><th>Pariu</th>"
            "<th>Cota Superbet</th><th>Cota corectă (Pinnacle)</th><th>EV</th><th>Miză sugerată</th>")
    rows = []
    for r in bets.sort_values(["date", "kickoff", "ev"], ascending=[True, True, False]).itertuples():
        cells = ([f"{r.date:%d.%m}"] if with_date else []) + [
            e(str(r.kickoff)), e(str(r.league)), f"{e(r.home)} &ndash; {e(r.away)}", e(str(r.selection)),
            f"{r.odds:.2f}", f"{r.fair_odds:.2f}", f"{r.ev * 100:+.1f}%", f"{r.stake:.2f}"]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return f"<table border='1' cellpadding='4' cellspacing='0'><tr>{head}</tr>{''.join(rows)}</table>"


def range_email_html(results: dict[str, DailyResult]) -> tuple[str, str]:
    """Subject and HTML for a multi-day run: counts per day, every value bet found, running record."""
    e = html.escape
    dates = sorted(results)
    bets = pd.concat([r.bets for r in results.values() if not r.bets.empty] or [pd.DataFrame()], ignore_index=True)
    compared = sum(r.compared for r in results.values())
    period = f"{pd.Timestamp(dates[0]):%d.%m} &ndash; {pd.Timestamp(dates[-1]):%d.%m.%Y}"
    weekday = ["Lu", "Ma", "Mi", "Jo", "Vi", "Sâ", "Du"]
    day_rows = "".join(f"<tr><td>{weekday[pd.Timestamp(d).weekday()]} {pd.Timestamp(d):%d.%m}</td><td>{results[d].compared}</td>"
                       f"<td>{len(results[d].unmatched)}</td><td>{len(results[d].bets)}</td></tr>" for d in dates)
    parts = [f"<h2>Value bets Superbet vs Pinnacle &mdash; {period}</h2>",
             f"<p>Meciuri comparate: {compared} &middot; pariuri cu valoare: <b>{len(bets)}</b>.</p>",
             "<table border='1' cellpadding='4' cellspacing='0'><tr><th>Zi</th><th>Comparate</th>"
             f"<th>Nepotrivite pe Pinnacle</th><th>Pariuri</th></tr>{day_rows}</table>"]
    if not bets.empty:
        parts.append("<h3>Pariuri</h3>" + bets_table(bets, with_date=True))
    parts.append("<p style='color:#666'>Cotele pentru zilele următoare se mai mișcă până la start; raportul zilnic "
                 "le reverifică. Estimare, nu garanție. Pariază doar sume pe care îți permiți să le pierzi.</p>")
    subject = f"Value bets ({len(bets)}) -- {pd.Timestamp(dates[0]):%d.%m}-{pd.Timestamp(dates[-1]):%d.%m.%Y}"
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
        parts.append(bets_table(result.bets, with_date=False))
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
