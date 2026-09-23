"""Walk-forward backtest: refit on the past, predict the next block, advance."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .blend import MarketBlender, devig
from .data import BTTS_FILENAME, attach_btts, load_btts, load_matches
from .dixon_coles import DEFAULT_XI, DixonColes, select_xi
from .markets import btts, prob_over, probs_1x2
from .staking import StakingConfig, best_outcome, simulate_bankroll, stake_size

EPS = 1e-12

MARKETS: dict[str, dict[str, list[str]]] = {
    "1x2": {"prob": ["p_h", "p_d", "p_a"], "odds": ["odds_h", "odds_d", "odds_a"],
            "close": ["close_h", "close_d", "close_a"], "labels": ["1", "X", "2"]},
    "ou25": {"prob": ["p_over", "p_under"], "odds": ["odds_over", "odds_under"],
             "close": ["close_over", "close_under"], "labels": ["Over 2.5", "Under 2.5"]},
    "btts": {"prob": ["p_gg", "p_ng"], "odds": ["odds_gg", "odds_ng"],
             "close": [], "labels": ["GG", "NG"]},
}


@dataclass
class BacktestConfig:
    """Walk-forward settings. `xi=None` selects xi from past data only."""

    start: pd.Timestamp
    staking: StakingConfig = field(default_factory=StakingConfig)
    refit_days: int = 7
    min_train: int = 300
    blend_min: int = 200
    xi: float | None = None
    xi_reselect_days: int = 180
    l2: float = 1.0
    devig_method: str = "proportional"


def outcomes(df: pd.DataFrame, market: str) -> np.ndarray:
    """Index of the winning outcome for each finished match."""
    hg, ag = df["hg"].to_numpy(), df["ag"].to_numpy()
    if market == "1x2":
        return np.where(hg > ag, 0, np.where(hg == ag, 1, 2))
    if market == "ou25":
        return np.where(hg + ag > 2.5, 0, 1)
    if market == "btts":
        return np.where((hg > 0) & (ag > 0), 0, 1)
    raise ValueError(market)


def model_probabilities(model: DixonColes, fixtures: pd.DataFrame) -> pd.DataFrame:
    """Model probabilities for every supported market."""
    m = model.predict_matrix(list(fixtures["home"]), list(fixtures["away"]))
    lam, mu = model.expected_goals(list(fixtures["home"]), list(fixtures["away"]))
    p1x2, over, gg = probs_1x2(m), prob_over(m, 2.5), btts(m)[:, 0]
    return pd.DataFrame({
        "lambda": lam, "mu": mu, "p_h": p1x2[:, 0], "p_d": p1x2[:, 1], "p_a": p1x2[:, 2],
        "p_over": over, "p_under": 1 - over, "p_gg": gg, "p_ng": 1 - gg,
    }, index=fixtures.index)


def _block_starts(first: pd.Timestamp, last: pd.Timestamp, step: int) -> list[pd.Timestamp]:
    return list(pd.date_range(first, last, freq=f"{step}D"))


def walk_forward_predictions(matches: pd.DataFrame, cfg: BacktestConfig) -> tuple[pd.DataFrame, dict]:
    """Out-of-sample model probabilities: each block is predicted by a model fit on earlier matches only."""
    out, xi_log = [], {}
    for league, lg in matches.groupby("league", sort=True):
        lg = lg.sort_values("date", kind="stable")
        if len(lg) <= cfg.min_train:
            continue
        model = DixonColes(xi=cfg.xi if cfg.xi is not None else DEFAULT_XI, l2=cfg.l2)
        last_xi_date, xi_log[league] = None, []
        first = lg["date"].iloc[cfg.min_train]
        for t in _block_starts(first, lg["date"].max(), cfg.refit_days):
            block = lg[(lg["date"] >= t) & (lg["date"] < t + pd.Timedelta(days=cfg.refit_days))]
            if block.empty:
                continue
            if cfg.xi is None and (last_xi_date is None or (t - last_xi_date).days >= cfg.xi_reselect_days):
                model.xi = select_xi(lg, t, min_train=cfg.min_train, l2=cfg.l2)
                last_xi_date = t
                xi_log[league].append({"date": str(t.date()), "xi": model.xi})
            model.fit(lg, ref_date=t)
            probs = model_probabilities(model, block)
            probs["fit_date"] = t
            out.append(block.join(probs))
    if not out:
        raise ValueError(f"not enough matches: every league needs more than {cfg.min_train}")
    preds = pd.concat(out).sort_values(["date", "league", "home"], kind="stable")
    return preds.reset_index(drop=True), xi_log


def add_market_probabilities(preds: pd.DataFrame, method: str = "proportional") -> pd.DataFrame:
    """De-vigged bet-time market probabilities (q_*) for each market."""
    preds = preds.copy()
    for spec in MARKETS.values():
        q = devig(preds[spec["odds"]].to_numpy(dtype=float), method)
        for col, values in zip(spec["prob"], q.T):
            preds["q" + col[1:]] = values
    return preds


def _market_cols(spec: dict, prefix: str) -> list[str]:
    return [prefix + c[1:] for c in spec["prob"]]


def walk_forward_blend(preds: pd.DataFrame, cfg: BacktestConfig) -> tuple[pd.DataFrame, dict]:
    """Blend P_model with P_market; each block uses a blender fit on finished matches only."""
    preds = preds.copy()
    coefs: dict[str, dict] = {}
    for market, spec in MARKETS.items():
        pm_cols, q_cols, f_cols = spec["prob"], _market_cols(spec, "q"), _market_cols(spec, "f")
        for c in f_cols:
            preds[c] = np.nan
        y_all = outcomes(preds, market)
        blender = MarketBlender(min_rows=cfg.blend_min)
        for t in _block_starts(preds["date"].min(), preds["date"].max(), cfg.refit_days):
            block = (preds["date"] >= t) & (preds["date"] < t + pd.Timedelta(days=cfg.refit_days))
            if not block.any():
                continue
            past = (preds["date"] < t).to_numpy()
            blender.fit(preds.loc[past, pm_cols].to_numpy(), preds.loc[past, q_cols].to_numpy(), y_all[past])
            preds.loc[block, f_cols] = blender.predict(preds.loc[block, pm_cols].to_numpy(),
                                                       preds.loc[block, q_cols].to_numpy())
        coefs[market] = {"n_train": blender.n_train, **blender.coefficients}
    return preds, coefs


def candidate_bets(preds: pd.DataFrame, market: str, prob_prefix: str, cfg: BacktestConfig) -> pd.DataFrame:
    """One bet per match and market: the outcome with max EV, if EV >= ev_min."""
    spec = MARKETS[market]
    rows = preds[preds["date"] >= cfg.start]
    p_cols = spec["prob"] if prob_prefix == "p" else _market_cols(spec, prob_prefix)
    p = rows[p_cols].to_numpy(dtype=float)
    odds = rows[spec["odds"]].to_numpy(dtype=float)
    idx, ev = best_outcome(p, odds, cfg.staking.ev_min)
    take = idx >= 0
    rows, idx, ev = rows[take], idx[take], ev[take]
    r = np.arange(len(rows))
    close = rows[spec["close"]].to_numpy(dtype=float)[r, idx] if spec["close"] else np.full(len(rows), np.nan)
    return pd.DataFrame({
        "date": rows["date"].to_numpy(), "league": rows["league"].to_numpy(),
        "home": rows["home"].to_numpy(), "away": rows["away"].to_numpy(), "market": market,
        "selection": np.array(spec["labels"])[idx],
        "p_model": rows[spec["prob"]].to_numpy(dtype=float)[r, idx],
        "p_market": rows[_market_cols(spec, "q")].to_numpy(dtype=float)[r, idx],
        "p": p[r, idx], "odds": odds[r, idx], "close_odds": close, "ev": ev,
        "won": outcomes(rows, market) == idx,
    })


def max_drawdown(curve: np.ndarray) -> float:
    """Largest peak-to-trough fall as a share of the peak."""
    if len(curve) == 0:
        return 0.0
    peaks = np.maximum.accumulate(curve)
    return float(np.max((peaks - curve) / peaks))


def betting_metrics(bets: pd.DataFrame, bankroll0: float) -> dict:
    """Bets, hit rate, yield, ROI on bankroll, max drawdown and CLV."""
    if bets.empty:
        return {"n_bets": 0, "hit_rate": None, "staked": 0.0, "profit": 0.0, "yield": None,
                "roi_bankroll": 0.0, "final_bankroll": bankroll0, "max_drawdown": 0.0,
                "clv_mean": None, "clv_n": 0, "clv_positive_share": None}
    daily = bets.groupby("date")["bankroll_after_day"].last().to_numpy()
    clv = (bets["odds"] / bets["close_odds"] - 1).dropna()
    staked, profit = float(bets["stake"].sum()), float(bets["profit"].sum())
    return {
        "n_bets": int(len(bets)), "hit_rate": float(bets["won"].mean()), "staked": staked,
        "profit": profit, "yield": profit / staked if staked else None,
        "roi_bankroll": profit / bankroll0, "final_bankroll": bankroll0 + profit,
        "max_drawdown": max_drawdown(np.concatenate([[bankroll0], daily])),
        "clv_mean": float(clv.mean()) if len(clv) else None, "clv_n": int(len(clv)),
        "clv_positive_share": float((clv > 0).mean()) if len(clv) else None,
    }


def prob_scores(p: np.ndarray, y: np.ndarray) -> dict:
    """Log loss and (multi-class) Brier score."""
    p = np.clip(np.asarray(p, float), EPS, 1)
    onehot = np.eye(p.shape[1])[y]
    return {"log_loss": float(-np.mean(np.log(p[np.arange(len(y)), y]))),
            "brier": float(np.mean(np.sum((p - onehot) ** 2, axis=1)))}


def market_evaluation(preds: pd.DataFrame, market: str, cfg: BacktestConfig) -> dict:
    """Model vs market vs blend on the same out-of-sample matches (date >= start)."""
    spec = MARKETS[market]
    rows = preds[preds["date"] >= cfg.start]
    y = outcomes(rows, market)
    pm = rows[spec["prob"]].to_numpy(dtype=float)
    q = rows[_market_cols(spec, "q")].to_numpy(dtype=float)
    f = rows[_market_cols(spec, "f")].to_numpy(dtype=float)
    ok = np.isfinite(q).all(axis=1) & np.isfinite(f).all(axis=1)
    result: dict = {"n_matches": int(len(rows)), "model_all": prob_scores(pm, y) if len(rows) else None}
    if rows.empty:
        result["note"] = "niciun meci prezis după data de start"
        return result
    if not ok.any():
        result["note"] = "fără cote de piață: doar acuratețea modelului (fără comparație cu piața și fără ROI)"
        return result
    result.update({
        "n_compared": int(ok.sum()), "model": prob_scores(pm[ok], y[ok]),
        "market": prob_scores(q[ok], y[ok]), "blend": prob_scores(f[ok], y[ok]),
    })
    if spec["close"]:
        close = rows[spec["close"]].to_numpy(dtype=float)
        c_ok = ok & np.isfinite(close).all(axis=1)
        if c_ok.any():
            result["pinnacle_close_baseline"] = prob_scores(devig(close[c_ok], cfg.devig_method), y[c_ok])
    beats = result["model"]["log_loss"] < result["market"]["log_loss"]
    result["model_beats_market"] = bool(beats)
    result["blend_beats_market"] = bool(result["blend"]["log_loss"] < result["market"]["log_loss"])
    result["verdict"] = (
        "Modelul bate piața la log loss walk-forward." if beats else
        "ATENȚIE: modelul NU bate piața la log loss walk-forward -> nu are edge demonstrat, "
        "indiferent de ROI-ul pe perioada testată."
    )
    return result


def run_backtest(data_dir: str | Path, cfg: BacktestConfig, out_dir: str | Path = "outputs",
                 odds_source: str = "B365") -> dict:
    """Full pipeline: load, walk-forward model + blend, bets, metrics, export."""
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    matches = load_matches(data_dir, odds_source)
    matches = attach_btts(matches, load_btts(data_dir / BTTS_FILENAME))
    preds, xi_log = walk_forward_predictions(matches, cfg)
    preds = add_market_probabilities(preds, cfg.devig_method)
    preds, coefs = walk_forward_blend(preds, cfg)

    has_gg_odds = bool(preds.loc[preds["date"] >= cfg.start, "odds_gg"].notna().any())
    bet_markets = ["1x2", "ou25"] + (["btts"] if has_gg_odds else [])
    candidates = pd.concat([candidate_bets(preds, m, "f", cfg) for m in bet_markets], ignore_index=True)
    bets = simulate_bankroll(candidates, cfg.staking)

    summary = {
        "config": {**{k: v for k, v in asdict(cfg).items() if k not in ("start", "staking")},
                   "start": str(cfg.start.date()), "staking": asdict(cfg.staking), "odds_source": odds_source},
        "data": {"matches": int(len(matches)), "predicted": int(len(preds)),
                 "evaluated": int((preds["date"] >= cfg.start).sum()),
                 "leagues": sorted(matches["league"].unique().tolist())},
        "xi_selected": xi_log,
        "blend_coefficients_last": coefs,
        "betting": betting_metrics(bets, cfg.staking.bankroll),
        "betting_by_market": {m: betting_metrics(bets[bets["market"] == m], cfg.staking.bankroll)
                              for m in bet_markets},
        "probability_quality": {m: market_evaluation(preds, m, cfg) for m in MARKETS},
        "btts_backtested": has_gg_odds,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    bets.to_csv(out_dir / "bets.csv", index=False)
    preds[preds["date"] >= cfg.start].to_csv(out_dir / "predictions.csv", index=False)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return summary


@dataclass
class FittedSystem:
    """Everything `predict` needs: one Dixon-Coles model per league and one blender per market."""

    models: dict[str, DixonColes]
    blenders: dict[str, MarketBlender]
    devig_method: str
    fitted_until: pd.Timestamp


def fit_system(data_dir: str | Path, cfg: BacktestConfig, odds_source: str = "B365") -> FittedSystem:
    """Fit final league models on all data; blenders on walk-forward (out-of-sample) predictions."""
    data_dir = Path(data_dir)
    matches = attach_btts(load_matches(data_dir, odds_source), load_btts(data_dir / BTTS_FILENAME))
    ref = matches["date"].max() + pd.Timedelta(days=1)
    preds, _ = walk_forward_predictions(matches, cfg)
    preds = add_market_probabilities(preds, cfg.devig_method)
    blenders = {}
    for market, spec in MARKETS.items():
        blenders[market] = MarketBlender(min_rows=cfg.blend_min).fit(
            preds[spec["prob"]].to_numpy(), preds[_market_cols(spec, "q")].to_numpy(), outcomes(preds, market))
    models = {}
    for league, lg in matches.groupby("league"):
        xi = cfg.xi if cfg.xi is not None else select_xi(lg, ref, min_train=cfg.min_train, l2=cfg.l2)
        models[league] = DixonColes(xi=xi, l2=cfg.l2).fit(lg, ref_date=ref)
    return FittedSystem(models, blenders, cfg.devig_method, ref)


def _league_for(system: FittedSystem, home: str, away: str) -> str | None:
    """League whose model knows both teams (or at least one)."""
    for need in (2, 1):
        for league, model in system.models.items():
            if (home in model.teams) + (away in model.teams) >= need:
                return league
    return None


def predict_fixtures(system: FittedSystem, fixtures: pd.DataFrame, staking: StakingConfig) -> pd.DataFrame:
    """Model, market and blended probabilities plus EV / suggested stake per market."""
    fixtures = fixtures.copy()
    fixtures["league"] = [lg if isinstance(lg, str) and lg in system.models else _league_for(system, h, a)
                          for lg, h, a in zip(fixtures["league"], fixtures["home"], fixtures["away"])]
    parts = []
    for league, fx in fixtures.groupby("league"):
        parts.append(fx.join(model_probabilities(system.models[league], fx)))
    if not parts:
        raise ValueError("no fixture matches a fitted league")
    out = add_market_probabilities(pd.concat(parts).sort_index(), system.devig_method)
    for market, spec in MARKETS.items():
        f = system.blenders[market].predict(out[spec["prob"]].to_numpy(), out[_market_cols(spec, "q")].to_numpy())
        out[_market_cols(spec, "f")] = f
        odds = out[spec["odds"]].to_numpy(dtype=float)
        idx, ev = best_outcome(f, odds, staking.ev_min)
        r = np.arange(len(out))
        pick = np.where(idx >= 0, np.array(spec["labels"])[np.clip(idx, 0, None)], "")
        p_pick = np.where(idx >= 0, f[r, np.clip(idx, 0, None)], np.nan)
        o_pick = np.where(idx >= 0, odds[r, np.clip(idx, 0, None)], np.nan)
        out[f"{market}_bet"] = pick
        out[f"{market}_ev"] = np.where(idx >= 0, ev, np.nan)
        out[f"{market}_stake"] = np.where(idx >= 0, stake_size(p_pick, o_pick, staking.bankroll, staking), 0.0)
    return out
