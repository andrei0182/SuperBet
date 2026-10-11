import numpy as np
import pandas as pd
import pytest

from superbet.paper import (TIER_LOWER, TIER_MAJOR, TIER_YOUTH, format_paper_report, league_tier, max_drawdown,
                            paper_report, paper_report_html)


def _settled(n_ok=4):
    rows = []
    for i in range(n_ok):
        rows.append({"date": pd.Timestamp("2026-09-26") + pd.Timedelta(days=i), "home": f"H{i}", "away": f"A{i}",
                     "league": "Poland - 3rd Liga" if i % 2 else "England - Premier League", "odds": 2.0,
                     "won": float(i % 2), "profit_flat": 1.0 if i % 2 else -1.0, "ev_close": 0.04 if i else -0.02,
                     "closing_status": "ok"})
    rows.append({"date": pd.Timestamp("2026-09-26"), "home": "Everton U21", "away": "Leeds U21",
                 "league": "England - Premier League 2", "odds": 3.0, "won": 1.0, "profit_flat": 2.0,
                 "ev_close": np.nan, "closing_status": "no_close"})
    rows.append({"date": pd.Timestamp("2026-09-27"), "home": "X", "away": "Y", "league": "Italy - Serie D",
                 "odds": 2.5, "won": np.nan, "profit_flat": np.nan, "ev_close": 0.1, "closing_status": "ok"})
    return pd.DataFrame(rows)


def test_league_tier():
    assert league_tier("England - Premier League", "Arsenal", "Chelsea") == TIER_MAJOR
    assert league_tier("Poland - U19 League", "Lech", "Legia") == TIER_YOUTH
    assert league_tier("Spain - Segunda RFEF", "Girona B", "Espanyol B") == TIER_YOUTH
    assert league_tier("Spain - Tercera Division", "UCAM Murcia II", "San Javier") == TIER_YOUTH
    assert league_tier("Brazil - Serie B", "Santos", "Sport") == TIER_LOWER  # "B" league is not a reserve team
    assert league_tier("Belgium - 2nd Amateur Division", "Tongeren", "RC Hades") == TIER_LOWER
    assert league_tier(np.nan, "A", "C") == TIER_LOWER


def test_max_drawdown():
    assert max_drawdown(np.array([10.0, -10.0, -10.0, 5.0]), 100.0) == pytest.approx(20 / 110)
    assert max_drawdown(np.array([1.0, 1.0]), 100.0) == 0.0


def test_paper_report_excludes_no_close_and_flags_small_sample():
    r = paper_report(_settled(), bankroll=1000, stake_share=0.01)
    assert r["bets"] == 6 and r["no_close"] == 1 and r["measured"] == 5
    assert r["ev_close_mean"] == pytest.approx((-0.02 + 0.04 * 3 + 0.1) / 5)
    assert r["ev_close_positive_share"] == pytest.approx(0.8)
    assert r["settled"] == 4 and r["pending"] == 1 and r["hit_rate"] == pytest.approx(0.5)
    assert r["roi"] == pytest.approx(0.0) and r["profit"] == pytest.approx(0.0)
    assert r["max_drawdown"] == pytest.approx(10 / 1000)  # first bet lost 1% of the bankroll
    assert not r["sample_ok"] and "insuficient (<300)" in r["sample_note"]
    assert set(r["by_tier"]) == {TIER_MAJOR, TIER_LOWER, TIER_YOUTH}
    assert r["by_tier"][TIER_YOUTH]["measured"] == 0 and r["by_tier"][TIER_YOUTH]["no_close"] == 1
    assert list(r["by_league"])[0] in ("England - Premier League", "Poland - 3rd Liga")
    text, html = format_paper_report(r), paper_report_html(r)
    assert "ATENȚIE: eșantion insuficient" in text and "Pe ligă" in text
    assert "Eșantion insuficient" in html and "ligi mari" in html


def test_paper_report_filters_and_full_sample():
    r = paper_report(_settled(), leagues=["poland"])
    assert r["bets"] == 2 and r["filter"]["leagues"] == ["poland"]
    assert paper_report(_settled(), tier=TIER_YOUTH)["bets"] == 1
    big = paper_report(_settled(), min_sample=3)
    assert big["sample_ok"] and big["sample_note"] is None
    assert "ATENȚIE" not in format_paper_report(big)


def test_paper_report_on_empty_and_old_logs():
    old = _settled().drop(columns=["league", "closing_status"])  # early logs had neither column
    r = paper_report(old)
    assert r["bets"] == 6 and r["measured"] == 6 and "(necunoscută)" in r["by_league"]
    empty = paper_report(_settled().iloc[0:0])
    assert empty["bets"] == 0 and empty["ev_close_mean"] is None and empty["max_drawdown"] == 0.0
    format_paper_report(empty)
