import numpy as np
import pytest

from superbet.dixon_coles import score_matrix
from superbet.markets import all_markets, asian_handicap, btts, over_under, prob_over, probs_1x2


@pytest.fixture
def m():
    return score_matrix(np.array([1.7, 0.8]), np.array([1.0, 1.9]), -0.06)


def test_1x2_sums_to_one(m):
    np.testing.assert_allclose(probs_1x2(m).sum(axis=-1), 1.0)
    assert probs_1x2(m)[0, 0] > probs_1x2(m)[0, 2]  # stronger home side


def test_over_under_consistency(m):
    ou = over_under(m, 2.5)
    np.testing.assert_allclose(ou[..., 1], 0.0)
    np.testing.assert_allclose(ou.sum(axis=-1), 1.0)
    assert np.all(prob_over(m, 1.5) > prob_over(m, 2.5))
    assert np.all(prob_over(m, 2.5) > prob_over(m, 3.5))


def test_over_manual():
    m = np.zeros((1, 3, 3))
    m[0, 0, 0], m[0, 2, 1], m[0, 1, 1] = 0.5, 0.3, 0.2
    assert prob_over(m, 2.5)[0] == pytest.approx(0.3)
    assert btts(m)[0, 0] == pytest.approx(0.5)


def test_btts_sums(m):
    np.testing.assert_allclose(btts(m).sum(axis=-1), 1.0)


def test_asian_handicap():
    m = np.zeros((1, 4, 4))
    m[0, 1, 0], m[0, 2, 0], m[0, 0, 0] = 0.5, 0.3, 0.2  # win by 1, win by 2, draw
    np.testing.assert_allclose(asian_handicap(m, -0.5)[0], [0.8, 0, 0.2])
    np.testing.assert_allclose(asian_handicap(m, -1.0)[0], [0.3, 0.5, 0.2])
    # -0.75 = half on -0.5, half on -1
    np.testing.assert_allclose(asian_handicap(m, -0.75)[0], [0.55, 0.25, 0.2])
    # AH 0 on home equals draw-no-bet
    np.testing.assert_allclose(asian_handicap(m, 0.0)[0], [0.8, 0.2, 0.0])


def test_all_markets_keys(m):
    out = all_markets(m)
    assert {"1x2", "btts", "over_2.5", "under_1.5"} <= set(out)
