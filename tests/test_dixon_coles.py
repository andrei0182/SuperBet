import numpy as np
import pandas as pd
import pytest
from scipy.optimize import check_grad

from superbet.dixon_coles import DixonColes, score_matrix, select_xi, tau
from synthetic import simulate_league


def test_tau_values():
    lam, mu, rho = 1.5, 1.1, -0.1
    x = np.array([0, 0, 1, 1, 2])
    y = np.array([0, 1, 0, 1, 3])
    out = tau(x, y, lam, mu, rho)
    np.testing.assert_allclose(out, [1 - lam * mu * rho, 1 + lam * rho, 1 + mu * rho, 1 - rho, 1.0])


def test_score_matrix_sums_to_one_and_1x2_sums_to_one():
    m = score_matrix(np.array([1.6, 0.4]), np.array([1.1, 2.9]), -0.08)
    assert m.shape == (2, 11, 11)
    np.testing.assert_allclose(m.sum(axis=(1, 2)), 1.0)
    p1 = np.tril(m, -1).sum(axis=(1, 2))
    px = np.trace(m, axis1=1, axis2=2)
    p2 = np.triu(m, 1).sum(axis=(1, 2))
    np.testing.assert_allclose(p1 + px + p2, 1.0)


def test_gradient_matches_numeric():
    df, _ = simulate_league(n_teams=6, rounds=2, seed=3)
    teams = sorted(set(df["home"]))
    idx = {t: i for i, t in enumerate(teams)}
    hi, ai = df["home"].map(idx).to_numpy(), df["away"].map(idx).to_numpy()
    x, y = df["hg"].to_numpy(float), df["ag"].to_numpy(float)
    w = np.linspace(0.5, 1, len(df))
    model = DixonColes(l2=0.5)
    rng = np.random.default_rng(0)
    theta = np.concatenate([rng.normal(0, 0.2, 12), [0.3, -0.05]])
    f = lambda t: model._objective(t, hi, ai, x, y, w, 6)[0]
    g = lambda t: model._objective(t, hi, ai, x, y, w, 6)[1]
    assert check_grad(f, g, theta) < 1e-4


def test_recovers_known_parameters():
    df, truth = simulate_league(n_teams=16, rounds=8, seed=1)
    model = DixonColes(xi=0.0, l2=0.1).fit(df)
    order = [model.teams.index(t) for t in truth["teams"]]
    assert np.corrcoef(model.attack[order], truth["attack"])[0, 1] > 0.9
    assert np.corrcoef(model.defence[order], truth["defence"])[0, 1] > 0.9
    assert model.home_adv == pytest.approx(truth["home_adv"], abs=0.08)
    assert abs(model.attack.sum()) < 1e-8


def test_unknown_team_gets_league_mean():
    df, _ = simulate_league(n_teams=6, rounds=4, seed=2)
    model = DixonColes(xi=0.0).fit(df)
    assert model.team_params("Promoted FC") == pytest.approx((model.attack.mean(), model.defence.mean()))


def test_fit_ignores_matches_after_ref_date():
    df, _ = simulate_league(n_teams=6, rounds=4, seed=4)
    cut = df["date"].iloc[len(df) // 2]
    a = DixonColes(xi=0.001).fit(df, ref_date=cut)
    b = DixonColes(xi=0.001).fit(df[df["date"] < cut], ref_date=cut)
    np.testing.assert_allclose(a.attack, b.attack)


def test_select_xi_returns_grid_value():
    df, _ = simulate_league(n_teams=10, rounds=8, seed=5)
    xi = select_xi(df, df["date"].max() + pd.Timedelta(days=1), n_folds=2, fold_days=60, min_train=150)
    assert xi in (0.0, 0.0005, 0.001, 0.002, 0.003)
