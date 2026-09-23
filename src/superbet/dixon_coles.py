"""Dixon-Coles bivariate Poisson model with time decay (Dixon & Coles, 1997)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

MAX_GOALS = 10
XI_GRID = (0.0, 0.0005, 0.001, 0.002, 0.003)
DEFAULT_XI = 0.002
RHO_BOUNDS = (-0.25, 0.25)
EPS = 1e-10


def tau(x: np.ndarray, y: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles low-score correction factor, vectorised."""
    x, y = np.asarray(x), np.asarray(y)
    lam, mu = np.broadcast_to(lam, x.shape), np.broadcast_to(mu, x.shape)
    out = np.ones(np.broadcast(x, y, lam).shape, dtype=float)
    out = np.where((x == 0) & (y == 0), 1.0 - lam * mu * rho, out)
    out = np.where((x == 0) & (y == 1), 1.0 + lam * rho, out)
    out = np.where((x == 1) & (y == 0), 1.0 + mu * rho, out)
    out = np.where((x == 1) & (y == 1), 1.0 - rho, out)
    return out


def time_weights(dates: pd.Series, ref_date: pd.Timestamp, xi: float) -> np.ndarray:
    """exp(-xi * days since match), relative to `ref_date`."""
    days = (ref_date - pd.to_datetime(dates)).dt.days.to_numpy(dtype=float)
    return np.exp(-xi * np.clip(days, 0, None))


def score_matrix(lam: np.ndarray, mu: np.ndarray, rho: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    """Score probabilities M[..., x, y] for goals 0..max_goals, renormalised to 1."""
    lam = np.atleast_1d(np.asarray(lam, dtype=float))
    mu = np.atleast_1d(np.asarray(mu, dtype=float))
    goals = np.arange(max_goals + 1)
    log_fact = gammaln(goals + 1)
    ph = np.exp(goals * np.log(lam[:, None]) - lam[:, None] - log_fact)
    pa = np.exp(goals * np.log(mu[:, None]) - mu[:, None] - log_fact)
    m = ph[:, :, None] * pa[:, None, :]
    m[:, 0, 0] *= 1.0 - lam * mu * rho
    m[:, 0, 1] *= 1.0 + lam * rho
    m[:, 1, 0] *= 1.0 + mu * rho
    m[:, 1, 1] *= 1.0 - rho
    m = np.clip(m, 0.0, None)
    return m / m.sum(axis=(1, 2), keepdims=True)


@dataclass
class DixonColes:
    """Dixon-Coles model for one league.

    Home goals ~ exp(a_home + d_away + h), away goals ~ exp(a_away + d_home),
    with sum(a) = 0 and a small L2 penalty on centred a and d.
    """

    xi: float = DEFAULT_XI
    l2: float = 1.0
    max_goals: int = MAX_GOALS
    teams: list[str] = field(default_factory=list)
    attack: np.ndarray = field(default_factory=lambda: np.zeros(0))
    defence: np.ndarray = field(default_factory=lambda: np.zeros(0))
    home_adv: float = 0.25
    rho: float = -0.05
    fitted_until: pd.Timestamp | None = None
    n_matches: int = 0

    def _unpack(self, theta: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, float, float]:
        return theta[:n], theta[n:2 * n], theta[2 * n], theta[2 * n + 1]

    def _objective(self, theta, hi, ai, x, y, w, n):
        """Negative weighted log-likelihood + L2 penalty, with analytic gradient."""
        a_raw, d, h, rho = self._unpack(theta, n)
        a = a_raw - a_raw.mean()
        log_lam = a[hi] + d[ai] + h
        log_mu = a[ai] + d[hi]
        lam, mu = np.exp(log_lam), np.exp(log_mu)

        t = tau(x, y, lam, mu, rho)
        t_safe = np.clip(t, EPS, None)
        ll = np.log(t_safe) + x * log_lam - lam + y * log_mu - mu
        dc = d - d.mean()
        nll = -np.sum(w * ll) + self.l2 * (np.sum(a ** 2) + np.sum(dc ** 2))

        # d tau / d lam, d tau / d mu, d tau / d rho
        m00, m01, m10, m11 = (x == 0) & (y == 0), (x == 0) & (y == 1), (x == 1) & (y == 0), (x == 1) & (y == 1)
        dt_dlam = np.where(m00, -mu * rho, np.where(m01, rho, 0.0))
        dt_dmu = np.where(m00, -lam * rho, np.where(m10, rho, 0.0))
        dt_drho = np.where(m00, -lam * mu, np.where(m01, lam, np.where(m10, mu, np.where(m11, -1.0, 0.0))))
        g_loglam = w * (x - lam + lam * dt_dlam / t_safe)
        g_logmu = w * (y - mu + mu * dt_dmu / t_safe)

        g_a = np.bincount(hi, g_loglam, n) + np.bincount(ai, g_logmu, n)
        g_d = np.bincount(ai, g_loglam, n) + np.bincount(hi, g_logmu, n)
        grad_a = -(g_a - g_a.mean()) + 2 * self.l2 * a
        grad_d = -g_d + 2 * self.l2 * dc
        grad_h = -np.sum(g_loglam)
        grad_rho = -np.sum(w * dt_drho / t_safe)
        return nll, np.concatenate([grad_a, grad_d, [grad_h, grad_rho]])

    def fit(self, matches: pd.DataFrame, ref_date: pd.Timestamp | None = None) -> "DixonColes":
        """Fit on matches (columns date, home, away, hg, ag) played before `ref_date`."""
        if ref_date is None:
            ref_date = matches["date"].max() + pd.Timedelta(days=1)
        matches = matches[matches["date"] < ref_date]
        if matches.empty:
            raise ValueError("no training matches")
        teams = sorted(set(matches["home"]) | set(matches["away"]))
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)
        hi = matches["home"].map(idx).to_numpy()
        ai = matches["away"].map(idx).to_numpy()
        x = matches["hg"].to_numpy(dtype=float)
        y = matches["ag"].to_numpy(dtype=float)
        w = time_weights(matches["date"], ref_date, self.xi)

        theta0 = self._warm_start(teams)
        bounds = [(None, None)] * (2 * n + 1) + [RHO_BOUNDS]
        res = minimize(self._objective, theta0, args=(hi, ai, x, y, w, n), jac=True,
                       method="L-BFGS-B", bounds=bounds, options={"maxiter": 500})
        a, d, h, rho = self._unpack(res.x, n)
        self.teams, self.attack, self.defence = teams, a - a.mean(), d.copy()
        self.home_adv, self.rho = float(h), float(rho)
        self.fitted_until, self.n_matches = ref_date, len(matches)
        return self

    def _warm_start(self, teams: list[str]) -> np.ndarray:
        """Initial parameters: previous fit where available, league mean otherwise."""
        old = {t: i for i, t in enumerate(self.teams)}
        mean_d = float(self.defence.mean()) if len(self.defence) else 0.2
        a = np.array([self.attack[old[t]] if t in old else 0.0 for t in teams])
        d = np.array([self.defence[old[t]] if t in old else mean_d for t in teams])
        return np.concatenate([a, d, [self.home_adv, self.rho]])

    def team_params(self, team: str) -> tuple[float, float]:
        """(attack, defence) for a team; unseen teams get the league mean."""
        if team in self.teams:
            i = self.teams.index(team)
            return float(self.attack[i]), float(self.defence[i])
        return float(self.attack.mean()), float(self.defence.mean())

    def expected_goals(self, home: list[str], away: list[str]) -> tuple[np.ndarray, np.ndarray]:
        """Expected goals (lambda, mu) for each fixture."""
        hp = np.array([self.team_params(t) for t in home]).reshape(-1, 2)
        ap = np.array([self.team_params(t) for t in away]).reshape(-1, 2)
        lam = np.exp(hp[:, 0] + ap[:, 1] + self.home_adv)
        mu = np.exp(ap[:, 0] + hp[:, 1])
        return lam, mu

    def predict_matrix(self, home: list[str], away: list[str]) -> np.ndarray:
        """Score matrices (n, G+1, G+1) for fixtures."""
        lam, mu = self.expected_goals(home, away)
        return score_matrix(lam, mu, self.rho, self.max_goals)


def result_index(hg: np.ndarray, ag: np.ndarray) -> np.ndarray:
    """0 = home win, 1 = draw, 2 = away win."""
    return np.where(hg > ag, 0, np.where(hg == ag, 1, 2))


def log_loss_1x2(model: DixonColes, matches: pd.DataFrame) -> float:
    """Mean 1X2 log loss of `model` on `matches`."""
    m = model.predict_matrix(list(matches["home"]), list(matches["away"]))
    probs = np.stack([np.tril(m, -1).sum(axis=(1, 2)),
                      np.trace(m, axis1=1, axis2=2),
                      np.triu(m, 1).sum(axis=(1, 2))], axis=1)
    y = result_index(matches["hg"].to_numpy(), matches["ag"].to_numpy())
    return float(-np.mean(np.log(np.clip(probs[np.arange(len(y)), y], EPS, 1))))


def select_xi(matches: pd.DataFrame, ref_date: pd.Timestamp, grid: tuple[float, ...] = XI_GRID,
              n_folds: int = 4, fold_days: int = 30, min_train: int = 300, l2: float = 1.0) -> float:
    """Choose xi by walk-forward validation on data strictly before `ref_date`.

    Each fold fits on everything before the fold window and scores the window.
    """
    history = matches[matches["date"] < ref_date]
    losses = {xi: [] for xi in grid}
    for k in range(n_folds, 0, -1):
        lo = ref_date - pd.Timedelta(days=k * fold_days)
        hi = lo + pd.Timedelta(days=fold_days)
        train, test = history[history["date"] < lo], history[(history["date"] >= lo) & (history["date"] < hi)]
        if len(train) < min_train or test.empty:
            continue
        for xi in grid:
            model = DixonColes(xi=xi, l2=l2).fit(train, ref_date=lo)
            losses[xi].append((log_loss_1x2(model, test), len(test)))
    scored = {xi: sum(l * n for l, n in v) / sum(n for _, n in v) for xi, v in losses.items() if v}
    if not scored:
        return DEFAULT_XI
    return min(scored, key=scored.get)
