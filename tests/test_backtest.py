import json
import pickle

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from superbet.backtest import BacktestConfig, betting_metrics, max_drawdown, prob_scores, run_backtest, \
    walk_forward_predictions
from superbet.cli import app
from superbet.staking import StakingConfig
from synthetic import simulate_league

FD_COLUMNS = {"date": "Date", "league": "Div", "home": "HomeTeam", "away": "AwayTeam", "hg": "FTHG", "ag": "FTAG",
              "odds_h": "B365H", "odds_d": "B365D", "odds_a": "B365A", "odds_over": "B365>2.5",
              "odds_under": "B365<2.5", "close_h": "PSCH", "close_d": "PSCD", "close_a": "PSCA",
              "close_over": "PC>2.5", "close_under": "PC<2.5"}


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("raw")
    df, _ = simulate_league(n_teams=10, rounds=6, seed=7, with_odds=True)
    out = df.rename(columns=FD_COLUMNS)
    out["Date"] = df["date"].dt.strftime("%d/%m/%Y")
    out.to_csv(d / "E0_sim.csv", index=False)
    return d


def test_max_drawdown():
    assert max_drawdown(np.array([100, 120, 90, 130, 65])) == pytest.approx(0.5)


def test_prob_scores_perfect_and_uniform():
    y = np.array([0, 1])
    assert prob_scores(np.eye(2)[y], y)["log_loss"] == pytest.approx(0, abs=1e-9)
    assert prob_scores(np.full((2, 3), 1 / 3), y)["log_loss"] == pytest.approx(np.log(3))


def test_betting_metrics_clv():
    bets = pd.DataFrame({"date": pd.to_datetime(["2023-01-01", "2023-01-02"]), "stake": [10.0, 10.0],
                         "profit": [10.0, -10.0], "won": [True, False], "odds": [2.0, 2.2],
                         "close_odds": [1.8, np.nan], "bankroll_after_day": [1010.0, 1000.0]})
    m = betting_metrics(bets, 1000)
    assert m["clv_mean"] == pytest.approx(2.0 / 1.8 - 1)
    assert m["clv_n"] == 1 and m["yield"] == 0.0


def test_walk_forward_never_uses_future(data_dir):
    from superbet.data import load_matches
    matches = load_matches(data_dir)
    cfg = BacktestConfig(start=pd.Timestamp("2021-01-01"), xi=0.001, refit_days=14, min_train=150)
    preds, _ = walk_forward_predictions(matches, cfg)
    assert (preds["fit_date"] <= preds["date"]).all()
    assert (preds["date"] < preds["fit_date"] + pd.Timedelta(days=14)).all()
    # predictions for a block do not change if later matches are removed
    cut = preds["fit_date"].iloc[len(preds) // 2]
    preds_cut, _ = walk_forward_predictions(matches[matches["date"] < cut + pd.Timedelta(days=14)], cfg)
    a = preds[preds["fit_date"] == cut].set_index(["date", "home"])["p_h"]
    b = preds_cut[preds_cut["fit_date"] == cut].set_index(["date", "home"])["p_h"]
    pd.testing.assert_series_equal(a, b)


def test_run_backtest_end_to_end(data_dir, tmp_path):
    start = pd.Timestamp("2021-03-01")
    cfg = BacktestConfig(start=start, xi=0.001, refit_days=14, min_train=150, blend_min=100,
                         staking=StakingConfig(ev_min=0.03))
    summary = run_backtest(data_dir, cfg, tmp_path)
    assert (tmp_path / "bets.csv").exists() and (tmp_path / "summary.json").exists()
    saved = json.loads((tmp_path / "summary.json").read_text())
    assert saved["betting"]["n_bets"] == summary["betting"]["n_bets"]
    bets = pd.read_csv(tmp_path / "bets.csv", parse_dates=["date"])
    assert (bets["date"] >= start).all()
    # each bet carries the odds and probability of its own match and selection
    preds = pd.read_csv(tmp_path / "predictions.csv", parse_dates=["date"])
    odds_col = {"1": "odds_h", "X": "odds_d", "2": "odds_a", "Over 2.5": "odds_over", "Under 2.5": "odds_under"}
    prob_col = {"1": "f_h", "X": "f_d", "2": "f_a", "Over 2.5": "f_over", "Under 2.5": "f_under"}
    merged = bets.merge(preds, on=["date", "home", "away"])
    assert len(merged) == len(bets) and len(bets) > 0
    for _, row in merged.iterrows():
        assert row["odds"] == pytest.approx(row[odds_col[row["selection"]]])
        assert row["p"] == pytest.approx(row[prob_col[row["selection"]]])
        assert row["p"] * row["odds"] - 1 >= 0.03 - 1e-9
    q = summary["probability_quality"]["1x2"]
    assert "model_beats_market" in q and "verdict" in q
    assert summary["btts_backtested"] is False
    assert "note" in summary["probability_quality"]["btts"]


def test_cli_fit_predict_backtest(data_dir, tmp_path):
    runner = CliRunner()
    model = tmp_path / "model.pkl"
    r = runner.invoke(app, ["fit", "--data", str(data_dir), "--out", str(model), "--min-train", "150",
                            "--refit-days", "30", "--xi", "0.001"])
    assert r.exit_code == 0, r.output
    assert "E0" in pickle.loads(model.read_bytes()).models

    fixtures = tmp_path / "fixtures.csv"
    pd.DataFrame([{"Date": "01/06/2030", "HomeTeam": "Team01", "AwayTeam": "Team02",
                   "B365H": 2.1, "B365D": 3.4, "B365A": 3.6, "B365>2.5": 1.9, "B365<2.5": 1.95},
                  {"Date": "01/06/2030", "HomeTeam": "Promoted", "AwayTeam": "Team03",
                   "B365H": 3.0, "B365D": 3.3, "B365A": 2.4}]).to_csv(fixtures, index=False)
    out = tmp_path / "pred.csv"
    r = runner.invoke(app, ["predict", "--model", str(model), "--fixtures", str(fixtures), "--out", str(out)])
    assert r.exit_code == 0, r.output
    pred = pd.read_csv(out)
    np.testing.assert_allclose(pred[["p_h", "p_d", "p_a"]].sum(axis=1), 1.0)

    r = runner.invoke(app, ["backtest", "--data", str(data_dir), "--start", "2021-03-01", "--min-train", "150",
                            "--refit-days", "30", "--xi", "0.001", "--out-dir", str(tmp_path / "bt")])
    assert r.exit_code == 0, r.output
    assert "log loss" in r.output
