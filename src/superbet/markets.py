"""Market probabilities derived from a score matrix M[..., home_goals, away_goals]."""

from __future__ import annotations

import numpy as np


def _grid(m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    g = np.arange(m.shape[-1])
    return g[:, None], g[None, :]


def probs_1x2(m: np.ndarray) -> np.ndarray:
    """[P(1), P(X), P(2)] along the last axis."""
    x, y = _grid(m)
    return np.stack([(m * (x > y)).sum(axis=(-2, -1)),
                     (m * (x == y)).sum(axis=(-2, -1)),
                     (m * (x < y)).sum(axis=(-2, -1))], axis=-1)


def over_under(m: np.ndarray, line: float = 2.5) -> np.ndarray:
    """[P(over), P(push), P(under)] for a total-goals line (push only on integer lines)."""
    x, y = _grid(m)
    total = x + y
    return np.stack([(m * (total > line)).sum(axis=(-2, -1)),
                     (m * (total == line)).sum(axis=(-2, -1)),
                     (m * (total < line)).sum(axis=(-2, -1))], axis=-1)


def prob_over(m: np.ndarray, line: float = 2.5) -> np.ndarray:
    """P(total goals > line)."""
    return over_under(m, line)[..., 0]


def btts(m: np.ndarray) -> np.ndarray:
    """[P(GG), P(NG)] — both teams to score."""
    gg = m[..., 1:, 1:].sum(axis=(-2, -1))
    return np.stack([gg, 1.0 - gg], axis=-1)


def asian_handicap(m: np.ndarray, line: float) -> np.ndarray:
    """Home side with handicap `line` (e.g. -0.5, -1, +0.25): [P(win), P(push), P(lose)].

    Quarter lines are split into the two adjacent half/integer lines; a
    half-win/half-loss contributes 0.5 to win (or lose) and 0.5 to push.
    """
    if abs(line * 4 - round(line * 4)) > 1e-9:
        raise ValueError("line must be a multiple of 0.25")
    if abs(line * 2 - round(line * 2)) > 1e-9:  # quarter line
        return 0.5 * (asian_handicap(m, line - 0.25) + asian_handicap(m, line + 0.25))
    x, y = _grid(m)
    margin = x - y + line
    return np.stack([(m * (margin > 0)).sum(axis=(-2, -1)),
                     (m * (margin == 0)).sum(axis=(-2, -1)),
                     (m * (margin < 0)).sum(axis=(-2, -1))], axis=-1)


def all_markets(m: np.ndarray, ou_lines: tuple[float, ...] = (1.5, 2.5, 3.5)) -> dict[str, np.ndarray]:
    """Dictionary of the standard markets for one or many score matrices."""
    out = {"1x2": probs_1x2(m), "btts": btts(m)}
    for line in ou_lines:
        ou = over_under(m, line)
        out[f"over_{line}"] = ou[..., 0]
        out[f"under_{line}"] = ou[..., 2]
    return out
