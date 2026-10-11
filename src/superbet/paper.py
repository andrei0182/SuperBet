"""Paper-trading report for the logged value bets: flat stake of 1% of the bankroll per bet.

The edge estimate is the closing EV (price taken x Pinnacle's no-vig closing probability - 1), on bets
whose closing line is a capture distinct from the selection one; bets without such a close are counted
apart and left out of every statistic. Realized results (hit rate, ROI, drawdown) are shown for the same
bets, but on a few hundred bets they are mostly noise.
"""

from __future__ import annotations

import html
import re

import numpy as np
import pandas as pd

MIN_SAMPLE = 300
STAKE_SHARE = 0.01

TIER_YOUTH = "tineret/rezerve"
TIER_LOWER = "ligi inferioare/amatori"
TIER_MAJOR = "ligi mari"
TIERS = (TIER_MAJOR, TIER_LOWER, TIER_YOUTH)

_YOUTH = re.compile(r"\b(u ?-?\d{2}|youth|junior|juniors|reserve|reserves|primavera|academy)\b", re.I)
_SECOND_TEAM = re.compile(r"\b(ii|b)$", re.I)  # "Girona B", "UCAM Murcia II" (team names only: "Serie B" is a league)
# Leagues where Pinnacle's price is sharp and soft-book limits are not tiny (Pinnacle league names).
_MAJOR = re.compile(
    r"^(england - (premier league|championship)|spain - la liga|germany - (bundesliga|2\. bundesliga)"
    r"|italy - serie a|france - ligue 1|netherlands - eredivisie|portugal - (primeira liga|liga portugal)"
    r"|belgium - (pro league|first division a)|turkey - super lig|scotland - premiership"
    r"|usa - major league soccer|brazil - serie a|mexico - liga mx|argentina - liga pro"
    r"|uefa - (champions league|europa league|conference league)"
    r"|.*(world cup|nations league|euro 20\d\d|copa america|africa cup of nations).*)$", re.I)


def league_tier(league: object, home: object = "", away: object = "") -> str:
    """Youth/reserve teams or leagues, the major leagues, or everything else (lower leagues, amateurs)."""
    league = "" if pd.isna(league) else str(league)
    teams = ["" if pd.isna(x) else str(x).strip() for x in (home, away)]
    if _YOUTH.search(" ".join([league] + teams)) or any(_SECOND_TEAM.search(t) for t in teams):
        return TIER_YOUTH
    if _MAJOR.match(league.strip()):
        return TIER_MAJOR
    return TIER_LOWER


def add_tier(settled: pd.DataFrame) -> pd.DataFrame:
    league = settled["league"] if "league" in settled else pd.Series("", index=settled.index)
    return settled.assign(tier=[league_tier(lg, h, a) for lg, h, a in zip(league, settled["home"], settled["away"])])


def filter_bets(settled: pd.DataFrame, leagues: list[str] | None = None, tier: str | None = None) -> pd.DataFrame:
    """Keep bets whose league contains any of `leagues` (case-insensitive) and/or of the given tier."""
    out = add_tier(settled)
    if leagues:
        lg = out["league"].fillna("").astype(str).str.lower() if "league" in out else pd.Series("", index=out.index)
        out = out[np.logical_or.reduce([lg.str.contains(x.lower(), regex=False) for x in leagues])]
    if tier:
        out = out[out["tier"] == tier]
    return out


def max_drawdown(profits: np.ndarray, bankroll: float) -> float:
    """Largest peak-to-trough fall of the bankroll curve, as a share of the peak."""
    curve = bankroll + np.concatenate([[0.0], np.cumsum(profits)])
    peaks = np.maximum.accumulate(curve)
    return float(np.max((peaks - curve) / peaks))


def _stats(bets: pd.DataFrame, bankroll: float, stake_share: float) -> dict:
    status = bets["closing_status"] if "closing_status" in bets else pd.Series("ok", index=bets.index)
    measured = bets[status == "ok"]
    ev = measured["ev_close"].dropna()
    done = measured[measured["won"].notna()]
    order = ["date"] + (["kickoff"] if "kickoff" in done else [])
    flat = done.sort_values(order, kind="stable")["profit_flat"].to_numpy(dtype=float)
    stake = bankroll * stake_share
    return {
        "bets": int(len(bets)),
        "no_close": int((status == "no_close").sum()),
        "missing_close": int((status == "missing_close").sum()),
        "measured": int(len(measured)),
        "ev_close_mean": float(ev.mean()) if len(ev) else None,
        "ev_close_se": float(ev.std(ddof=1) / np.sqrt(len(ev))) if len(ev) > 1 else None,
        "ev_close_positive_share": float((ev > 0).mean()) if len(ev) else None,
        "close_median_minutes_before": (float(measured["close_minutes_before"].median())
                                        if "close_minutes_before" in measured and measured["close_minutes_before"].notna().any()
                                        else None),
        "settled": int(len(done)),
        "pending": int(measured["won"].isna().sum()),
        "hit_rate": float(done["won"].mean()) if len(done) else None,
        "avg_odds": float(done["odds"].mean()) if len(done) else None,
        "roi": float(flat.mean()) if len(flat) else None,
        "profit": float(flat.sum() * stake),
        "max_drawdown": max_drawdown(flat * stake, bankroll) if len(flat) else 0.0,
    }


def paper_report(settled: pd.DataFrame, bankroll: float = 1000.0, stake_share: float = STAKE_SHARE,
                 min_sample: int = MIN_SAMPLE, leagues: list[str] | None = None, tier: str | None = None) -> dict:
    """Flat-stake paper record overall, per tier and per league (optionally filtered by league/tier)."""
    bets = filter_bets(settled, leagues, tier)
    report = {"bankroll": bankroll, "stake_share": stake_share, "stake": bankroll * stake_share,
              "min_sample": min_sample, "filter": {"leagues": leagues or [], "tier": tier},
              **_stats(bets, bankroll, stake_share)}
    report["sample_ok"] = report["measured"] >= min_sample
    report["sample_note"] = (None if report["sample_ok"] else
                             f"eșantion insuficient (<{min_sample}): {report['measured']} pariuri cu închidere "
                             "distinctă; nicio concluzie despre avantaj, fără miză reală")
    report["by_tier"] = {t: _stats(bets[bets["tier"] == t], bankroll, stake_share)
                         for t in TIERS if (bets["tier"] == t).any()}
    league = bets["league"].fillna("").replace("", "(necunoscută)") if "league" in bets else \
        pd.Series("(necunoscută)", index=bets.index)
    report["by_league"] = {lg: _stats(g, bankroll, stake_share)
                           for lg, g in sorted(bets.groupby(league), key=lambda kv: -len(kv[1]))}
    return report


def _pct(v: float | None, sign: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v * 100:+.1f}%" if sign else f"{v * 100:.1f}%"


def _odds(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def _ev(s: dict) -> str:
    se = f" ± {s['ev_close_se'] * 100:.1f}%" if s["ev_close_se"] else ""
    return f"{_pct(s['ev_close_mean'], True)}{se}"


def format_paper_report(r: dict, top_leagues: int = 15) -> str:
    """Console version of the paper-trading report."""
    flt = r["filter"]
    lines = [f"Paper trading, miză fixă {r['stake_share'] * 100:g}% din bankroll ({r['stake']:.2f} din {r['bankroll']:.0f})"
             + (f" | filtru ligă: {', '.join(flt['leagues'])}" if flt["leagues"] else "")
             + (f" | nivel: {flt['tier']}" if flt["tier"] else "")]
    if r["sample_note"]:
        lines.append(f"ATENȚIE: {r['sample_note']}.")
    lines += [
        f"Pariuri: {r['bets']} | cu închidere distinctă: {r['measured']} | excluse no_close: {r['no_close']} "
        f"| fără cotă de închidere: {r['missing_close']}",
        f"EV la închidere (Pinnacle de-vig power): {_ev(r)} | pozitive: {_pct(r['ev_close_positive_share'])}"
        + (f" | închiderea luată în mediană cu {r['close_median_minutes_before']:.0f} min înainte de start"
           if r["close_median_minutes_before"] is not None else ""),
        f"Decontate: {r['settled']} (în așteptare {r['pending']}) | hit rate {_pct(r['hit_rate'])} "
        f"| cotă medie {_odds(r['avg_odds'])} | ROI {_pct(r['roi'], True)} | profit {r['profit']:+.2f} "
        f"| drawdown maxim {_pct(r['max_drawdown'])}",
        "", "Pe nivel:",
    ]
    for t, s in r["by_tier"].items():
        lines.append(f"  {t}: {s['measured']} măsurate (din {s['bets']}), EV închidere {_ev(s)}, "
                     f"ROI {_pct(s['roi'], True)} pe {s['settled']}")
    lines.append(f"\nPe ligă (primele {top_leagues}):")
    for lg, s in list(r["by_league"].items())[:top_leagues]:
        lines.append(f"  {lg}: {s['bets']} pariuri, {s['measured']} măsurate, EV închidere {_ev(s)}, "
                     f"ROI {_pct(s['roi'], True)} pe {s['settled']}")
    return "\n".join(lines)


def paper_report_html(r: dict, top_leagues: int = 10) -> str:
    """Email section with the same numbers."""
    e = html.escape
    parts = [f"<h3>Paper trading (miză fixă {r['stake_share'] * 100:g}% din bankroll)</h3>"]
    if r["sample_note"]:
        parts.append(f"<p style='color:#b00'><b>Eșantion insuficient (&lt;{r['min_sample']})</b>: "
                     f"{r['measured']} pariuri cu închidere distinctă. Nicio concluzie despre avantaj, fără miză reală.</p>")
    parts.append(
        f"<p>Pariuri: {r['bets']} &middot; cu închidere distinctă: <b>{r['measured']}</b> &middot; "
        f"excluse (închiderea = captura de selecție): {r['no_close']} &middot; fără cotă de închidere: "
        f"{r['missing_close']}<br>EV la închidere (Pinnacle, de-vig power): <b>{e(_ev(r))}</b>, pozitive "
        f"{_pct(r['ev_close_positive_share'])}<br>Decontate: {r['settled']} (în așteptare {r['pending']}) &middot; "
        f"hit rate {_pct(r['hit_rate'])} &middot; ROI {_pct(r['roi'], True)} &middot; profit {r['profit']:+.2f} "
        f"&middot; drawdown maxim {_pct(r['max_drawdown'])}</p>")
    rows = "".join(f"<tr><td>{e(t)}</td><td>{s['bets']}</td><td>{s['measured']}</td><td>{e(_ev(s))}</td>"
                   f"<td>{s['settled']}</td><td>{_pct(s['roi'], True)}</td></tr>" for t, s in r["by_tier"].items())
    rows += "".join(f"<tr><td>&nbsp;&nbsp;{e(lg)}</td><td>{s['bets']}</td><td>{s['measured']}</td><td>{e(_ev(s))}</td>"
                    f"<td>{s['settled']}</td><td>{_pct(s['roi'], True)}</td></tr>"
                    for lg, s in list(r["by_league"].items())[:top_leagues])
    parts.append("<table border='1' cellpadding='4' cellspacing='0'><tr><th>Nivel / ligă</th><th>Pariuri</th>"
                 "<th>Cu închidere</th><th>EV la închidere</th><th>Decontate</th><th>ROI</th></tr>"
                 f"{rows}</table>")
    return "\n".join(parts)
