"""Synthetic league generator used by the tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from superbet.dixon_coles import score_matrix


def simulate_league(n_teams: int = 12, rounds: int = 4, seed: int = 0, home_adv: float = 0.3,
                    rho: float = -0.08, start: str = "2020-08-01", league: str = "E0",
                    with_odds: bool = False) -> tuple[pd.DataFrame, dict]:
    """Simulate `rounds` double round-robins from known Dixon-Coles parameters."""
    rng = np.random.default_rng(seed)
    teams = [f"Team{i:02d}" for i in range(n_teams)]
    attack = rng.normal(0, 0.3, n_teams)
    attack -= attack.mean()
    defence = rng.normal(0.1, 0.25, n_teams)
    rows = []
    day = pd.Timestamp(start)
    for _ in range(rounds):
        for i in range(n_teams):
            for j in range(n_teams):
                if i == j:
                    continue
                lam = np.exp(attack[i] + defence[j] + home_adv)
                mu = np.exp(attack[j] + defence[i])
                m = score_matrix(lam, mu, rho)[0]
                cell = rng.choice(m.size, p=m.ravel())
                hg, ag = divmod(cell, m.shape[1])
                row = {"date": day, "league": league, "home": teams[i], "away": teams[j], "hg": hg, "ag": ag}
                if with_odds:
                    p1, px, p2 = np.tril(m, -1).sum(), np.trace(m), np.triu(m, 1).sum()
                    idx = np.add.outer(np.arange(m.shape[0]), np.arange(m.shape[1]))
                    po = m[idx >= 3].sum()
                    noise = np.exp(rng.normal(0, 0.05, 5))
                    margin = 1.05
                    row.update(odds_h=1 / (p1 * margin) * noise[0], odds_d=1 / (px * margin) * noise[1],
                               odds_a=1 / (p2 * margin) * noise[2], odds_over=1 / (po * margin) * noise[3],
                               odds_under=1 / ((1 - po) * margin) * noise[4],
                               close_h=1 / (p1 * 1.02), close_d=1 / (px * 1.02), close_a=1 / (p2 * 1.02),
                               close_over=1 / (po * 1.02), close_under=1 / ((1 - po) * 1.02))
                rows.append(row)
                day += pd.Timedelta(hours=16)
    df = pd.DataFrame(rows)
    df["date"] = df["date"].dt.normalize()
    truth = {"teams": teams, "attack": attack, "defence": defence, "home_adv": home_adv, "rho": rho}
    return df, truth
