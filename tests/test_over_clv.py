import numpy as np
import pandas as pd
import pytest

from superbet.blend import devig
from superbet.over_clv import over_closing, over_closing_html


def _closing():
    return pd.DataFrame({"Date": ["23/09/2026", "23/09/2026", "24/09/2026"],
                         "HomeTeam": ["Chesterfield", "Bromley U21", "Leeds"],
                         "AwayTeam": ["Manchester City U21", "Ipswich U21", "Hull"],
                         "PC>2.5": [1.40, 1.30, 1.90], "PC<2.5": [2.90, 3.40, 1.95]})


def _recs():
    return pd.DataFrame({"date": ["2026-09-23", "2026-09-23", "2026-09-23", "2026-09-23"],
                         "home_team": ["Chesterfield FC", "Bromley U21", "Nobody", "Leeds"],
                         "away_team": ["Man City U21", "Ipswich Town U21", "Else", "Hull"],
                         "odds_over": ["1.46", "1.29", "1.8", "1.9"], "result": ["over", "over", "under", "pending"]})


def test_over_closing_pairs_and_measures():
    out = over_closing(_recs(), _closing())
    fair = devig(np.array([[1.40, 2.90]]), "power")[0, 0]
    assert out.loc[0, "close_over"] == 1.40
    assert out.loc[0, "ev_close"] == pytest.approx(1.46 * fair - 1)
    assert out.loc[0, "clv"] == pytest.approx(1.46 / 1.40 - 1)
    assert out.loc[1, "close_over"] == 1.30  # fuzzy "Ipswich Town U21" vs "Ipswich U21"
    assert np.isnan(out.loc[2, "ev_close"])  # no such match on Pinnacle
    assert np.isnan(out.loc[3, "ev_close"])  # Leeds-Hull is on 24.09 at Pinnacle, not 23.09


def test_over_closing_rejects_far_prices():
    recs = _recs().iloc[:1].assign(odds_over="3.5")  # implied 29% vs Pinnacle ~66%: not the same market
    assert np.isnan(over_closing(recs, _closing()).loc[0, "ev_close"])


def test_html_sections():
    html = over_closing_html(over_closing(_recs(), _closing()))
    assert "2 cu linie de închidere" in html and "Chesterfield" in html
    empty = over_closing_html(over_closing(_recs().iloc[2:3], _closing()))
    assert "încă nicio recomandare" in empty


def test_daily_recommendations_section_is_optional(tmp_path, monkeypatch):
    import daily_recommendations as dr

    monkeypatch.setattr(dr, "LOG_PATH", str(tmp_path / "missing.csv"))
    assert dr.closing_line_html(None) == ""
    assert dr.closing_line_html(str(tmp_path / "nope.csv")) == ""
