"""Command line: superbet fit / predict / backtest."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import pandas as pd
import typer

from .backtest import BacktestConfig, fit_system, predict_fixtures, run_backtest
from .data import load_fixtures
from .staking import StakingConfig

app = typer.Typer(help="Dixon-Coles + market blend + Kelly, with walk-forward backtest.", no_args_is_help=True)


def _config(start: str, ev_min: float, kelly: float, cap: float, bankroll: float, refit_days: int,
            min_train: int, xi: float | None, devig_method: str) -> BacktestConfig:
    staking = StakingConfig(ev_min=ev_min, kelly=kelly, cap=cap, bankroll=bankroll)
    return BacktestConfig(start=pd.Timestamp(start), staking=staking, refit_days=refit_days,
                          min_train=min_train, xi=xi, devig_method=devig_method)


@app.command()
def fit(
    data: Path = typer.Option(Path("data/raw"), help="Folder with football-data CSV files."),
    out: Path = typer.Option(Path("outputs/model.pkl"), help="Where to save the fitted system."),
    refit_days: int = typer.Option(7, help="Walk-forward step used to train the blenders."),
    min_train: int = typer.Option(300, help="Minimum training matches per league."),
    xi: float | None = typer.Option(None, help="Fixed time decay; default: chosen by walk-forward validation."),
    odds_source: str = typer.Option("B365", help="Bet-time odds: B365 or Avg."),
    devig_method: str = typer.Option("proportional", help="proportional or power."),
) -> None:
    """Fit one Dixon-Coles model per league plus the market blenders."""
    cfg = _config("1900-01-01", 0.03, 0.25, 0.02, 1000.0, refit_days, min_train, xi, devig_method)
    system = fit_system(data, cfg, odds_source)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pickle.dumps(system))
    for league, model in system.models.items():
        typer.echo(f"{league}: {len(model.teams)} echipe, {model.n_matches} meciuri, xi={model.xi}, "
                   f"h={model.home_adv:.3f}, rho={model.rho:.3f}")
    typer.echo(f"Model salvat în {out}")


@app.command()
def predict(
    model: Path = typer.Option(Path("outputs/model.pkl"), help="File produced by `superbet fit`."),
    fixtures: Path = typer.Option(..., help="CSV: Date,HomeTeam,AwayTeam[,Div] + odds (B365H.., B365>2.5.., gg_yes/gg_no)."),
    out: Path = typer.Option(Path("outputs/predictions_fixtures.csv")),
    ev_min: float = typer.Option(0.03), kelly: float = typer.Option(0.25), cap: float = typer.Option(0.02),
    bankroll: float = typer.Option(1000.0), odds_source: str = typer.Option("B365"),
) -> None:
    """Probabilities, value and suggested stakes for upcoming fixtures."""
    system = pickle.loads(model.read_bytes())
    staking = StakingConfig(ev_min=ev_min, kelly=kelly, cap=cap, bankroll=bankroll)
    result = predict_fixtures(system, load_fixtures(fixtures, odds_source), staking)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False)
    cols = ["date", "home", "away", "p_h", "p_d", "p_a", "p_over", "p_gg", "f_h", "f_d", "f_a", "f_over",
            "1x2_bet", "1x2_ev", "1x2_stake", "ou25_bet", "ou25_ev", "ou25_stake"]
    with pd.option_context("display.width", 200, "display.max_columns", None):
        typer.echo(result[cols].round({c: 3 for c in cols[3:]}).to_string(index=False))
    typer.echo(f"\nSalvat în {out}")


@app.command()
def backtest(
    data: Path = typer.Option(Path("data/raw")),
    start: str = typer.Option(..., help="First date on which bets are allowed (YYYY-MM-DD)."),
    ev_min: float = typer.Option(0.03), kelly: float = typer.Option(0.25),
    cap: float = typer.Option(0.02, help="Max stake per bet as share of bankroll."),
    bankroll: float = typer.Option(1000.0), refit_days: int = typer.Option(7),
    min_train: int = typer.Option(300), xi: float | None = typer.Option(None),
    odds_source: str = typer.Option("B365"), devig_method: str = typer.Option("proportional"),
    out_dir: Path = typer.Option(Path("outputs")),
) -> None:
    """Walk-forward backtest; writes bets.csv, predictions.csv and summary.json."""
    cfg = _config(start, ev_min, kelly, cap, bankroll, refit_days, min_train, xi, devig_method)
    summary = run_backtest(data, cfg, out_dir, odds_source)
    typer.echo(format_summary(summary))
    typer.echo(f"\nRezultate în {out_dir}/ (bets.csv, predictions.csv, summary.json)")


@app.command()
def value(
    odds: Path = typer.Option(..., help="Sharp odds (football-data layout: Date,HomeTeam,AwayTeam,PSH,PSD,PSA,P>2.5,P<2.5 + soft books)."),
    books: str = typer.Option("SB", help="Soft book codes, comma separated (columns <code>H/D/A, <code>>2.5/<2.5)."),
    superbet_xlsx: Path | None = typer.Option(None, help="Superbet scraper export; its odds become book SB."),
    date: str | None = typer.Option(None, help="Match date for --superbet-xlsx (YYYY-MM-DD)."),
    team_map: Path | None = typer.Option(None, help="CSV superbet_name,name for teams spelled differently."),
    ev_min: float = typer.Option(0.02), max_odds: float = typer.Option(8.0),
    kelly: float = typer.Option(0.25), cap: float = typer.Option(0.02), bankroll: float = typer.Option(1000.0),
    log: Path = typer.Option(Path("outputs/value_log.csv"), help="Bet log (appended; first price kept)."),
) -> None:
    """Value bets: soft-book price above the de-vigged sharp (Pinnacle) price."""
    from .value import append_log, find_value, join_sources, load_odds_table, load_superbet_excel

    table = load_odds_table(odds)
    if superbet_xlsx is not None:
        if date is None:
            raise typer.BadParameter("--date is required with --superbet-xlsx")
        mapping = None
        if team_map is not None:
            m = pd.read_csv(team_map)
            mapping = dict(zip(m["superbet_name"], m["name"]))
        table, unmatched = join_sources(table, load_superbet_excel(superbet_xlsx, date), mapping)
        if len(unmatched):
            typer.echo(f"{len(unmatched)} meciuri Superbet fără pereche în cotele sharp (completează --team-map):")
            typer.echo(unmatched.head(20).to_string(index=False))
    staking = StakingConfig(ev_min=ev_min, kelly=kelly, cap=cap, bankroll=bankroll)
    bets = find_value(table, [b.strip() for b in books.split(",")], staking, ev_min, max_odds)
    if bets.empty:
        typer.echo("Niciun pariu cu valoare.")
        return
    append_log(bets, log)
    with pd.option_context("display.width", 200, "display.max_columns", None):
        typer.echo(bets.round({c: 3 for c in ("odds", "fair_p", "fair_odds", "ev", "stake")}).to_string(index=False))
    typer.echo(f"\n{len(bets)} pariuri adăugate în {log}")


@app.command("pinnacle-snapshot")
def pinnacle_snapshot(
    out: Path = typer.Option(Path("outputs/sharp.csv"), help="Current Pinnacle prices, input for `superbet value --odds`."),
    history: Path | None = typer.Option(Path("outputs/pinnacle_snapshots.csv"),
                                        help="Every snapshot is appended here; the last one before kick-off is the closing line."),
    days: int = typer.Option(3, help="Keep matches starting within this many days."),
) -> None:
    """Download Pinnacle prematch soccer odds from pinnapi.com (key in the PINNAPI_KEY environment variable)."""
    from .pinnacle import snapshot

    df = snapshot(out, history, days)
    typer.echo(f"{len(df)} meciuri ({df['League'].nunique()} ligi) salvate în {out}"
               + (f", adăugate în {history}" if history else ""))


@app.command("value-settle")
def value_settle(
    log: Path = typer.Option(Path("outputs/value_log.csv")),
    results: Path | None = typer.Option(None, help="Results with FTHG,FTAG (+ closing PSCH.. if no --closing-history)."),
    closing_history: Path | None = typer.Option(None, help="pinnacle-snapshot history: closing = last snapshot before kick-off."),
    out: Path = typer.Option(Path("outputs/value_settled.csv")),
) -> None:
    """Settle logged bets and measure them against the Pinnacle closing line."""
    from .pinnacle import attach_closing, closing_lines
    from .value import load_odds_table, read_log, settle, summarize

    if results is None and closing_history is None:
        raise typer.BadParameter("give --results and/or --closing-history")
    res = load_odds_table(results) if results is not None else None
    if closing_history is not None:
        res = attach_closing(res, closing_lines(closing_history))
    settled = settle(read_log(log), res)
    out.parent.mkdir(parents=True, exist_ok=True)
    settled.to_csv(out, index=False)
    typer.echo(format_value_summary(summarize(settled)))


@app.command("value-backtest")
def value_backtest_cmd(
    data: Path = typer.Option(Path("data/raw")),
    books: str = typer.Option("B365,BW,WH,VC,IW,1XB", help="Soft books to shop across."),
    start: str | None = typer.Option(None), ev_min: float = typer.Option(0.02), max_odds: float = typer.Option(8.0),
    kelly: float = typer.Option(0.25), cap: float = typer.Option(0.02), bankroll: float = typer.Option(1000.0),
    out: Path = typer.Option(Path("outputs/value_backtest.csv")),
) -> None:
    """Historical value test: bets from early odds, judged at Pinnacle closing (football-data files)."""
    from .value import summarize, value_backtest

    staking = StakingConfig(ev_min=ev_min, kelly=kelly, cap=cap, bankroll=bankroll)
    settled = value_backtest(data, [b.strip() for b in books.split(",")], start, staking, ev_min, max_odds)
    out.parent.mkdir(parents=True, exist_ok=True)
    settled.to_csv(out, index=False)
    typer.echo(format_value_summary(summarize(settled)))
    by = settled.groupby(settled["date"].dt.year).agg(bets=("ev", "size"), ev_close=("ev_close", "mean"),
                                                       yield_flat=("profit_flat", "mean"))
    typer.echo("\nPe ani:\n" + by.round(4).to_string())


def format_value_summary(s: dict) -> str:
    """Human-readable value-betting report; closing EV first, since realized profit is mostly noise."""
    se = lambda v: "-" if v is None else f"± {v * 100:.2f}%"  # noqa: E731
    lines = [
        f"Pariuri: {s['bets']} (decontate {s['settled']}, cu cote de închidere {s['with_closing_odds']})",
        f"EV la închidere (Pinnacle, de-vig): {_fmt(s['ev_close_mean'], True)} {se(s['ev_close_se'])}  <- estimarea avantajului",
        f"CLV mediu: {_fmt(s['clv_mean'], True)}, pariuri cu CLV pozitiv: {_fmt(s['clv_positive_share'], True)}",
        f"Hit rate: {_fmt(s['hit_rate'], True)} | yield mize egale: {_fmt(s['yield_flat'], True)} {se(s['yield_flat_se'])}"
        f" | yield Kelly: {_fmt(s['yield_kelly'], True)} | profit: {s['profit']:.2f}",
    ]
    if s["ev_close_mean"] is not None and s["ev_close_se"]:
        z = s["ev_close_mean"] / s["ev_close_se"]
        verdict = ("EV la închidere clar pozitiv: prețurile luate au bătut linia de închidere."
                   if z > 2 else "EV la închidere neconcludent: încă nu există dovadă de avantaj.")
        lines.append(verdict)
    lines.append("Profitul realizat are nevoie de mii de pariuri ca să confirme un avantaj de 2-3%.")
    return "\n".join(lines)


def _fmt(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.2f}%" if pct else f"{v:.4f}"


def format_summary(summary: dict) -> str:
    """Human-readable report."""
    b = summary["betting"]
    lines = [
        f"Meciuri: {summary['data']['matches']}, prezise walk-forward: {summary['data']['predicted']}, "
        f"evaluate (>= start): {summary['data']['evaluated']}",
        f"Pariuri: {b['n_bets']} | hit rate {_fmt(b['hit_rate'], True)} | yield {_fmt(b['yield'], True)} | "
        f"ROI bankroll {_fmt(b['roi_bankroll'], True)} | max drawdown {_fmt(b['max_drawdown'], True)} | "
        f"CLV mediu {_fmt(b['clv_mean'], True)} (n={b['clv_n']})",
    ]
    for market, bm in summary["betting_by_market"].items():
        lines.append(f"  {market}: {bm['n_bets']} pariuri, yield {_fmt(bm['yield'], True)}, "
                     f"CLV {_fmt(bm['clv_mean'], True)}")
    lines.append("\nCalitatea probabilităților (log loss / Brier):")
    for market, q in summary["probability_quality"].items():
        if "model" not in q:
            m = q.get("model_all")
            lines.append(f"  {market}: model {_fmt(m and m['log_loss'])} / {_fmt(m and m['brier'])} — {q.get('note', '')}")
            continue
        lines.append(f"  {market} (n={q['n_compared']}): model {_fmt(q['model']['log_loss'])} / "
                     f"{_fmt(q['model']['brier'])} | piață {_fmt(q['market']['log_loss'])} / "
                     f"{_fmt(q['market']['brier'])} | blend {_fmt(q['blend']['log_loss'])} / {_fmt(q['blend']['brier'])}")
        lines.append(f"    {q['verdict']}")
    if not summary["btts_backtested"]:
        lines.append("\nGG: fără data/raw/btts_odds.csv -> evaluat doar ca acuratețe, fără ROI.")
    return "\n".join(lines)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
