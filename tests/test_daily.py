import pandas as pd

from superbet.daily import email_html, run_daily
from superbet.staking import StakingConfig
from test_pinnacle import NOW, _event


def _payload(home_price):
    return {"events": [
        _event(1, "Arsenal", "Chelsea", "2026-09-26T14:00:00Z", (home_price, 3.6, 4.2), (1.9, 2.0)),
        _event(2, "Japan", "Uruguay", "2026-09-26T11:00:00Z", (1.83, 3.6, 4.13)),
    ]}


def _superbet(path):
    pd.DataFrame({"League": ["x", "y", "z"], "Home Team": ["Arsenal FC", "Japonia", "Steaua"],
                  "Away Team": ["Chelsea", "Uruguay", "Rapid"], "Odds 1": [2.3, 1.85, 2.0], "Odds X": [3.4, 3.5, 3.2],
                  "Odds 2": [3.9, 4.2, 3.8], "O/U Line": [2.5, 2.5, 2.5], "Odds Over": [1.85, 1.9, 1.8],
                  "Odds Under": [1.95, 1.9, 2.0]}).to_excel(path, index=False)


def test_run_daily_logs_value_and_reports(tmp_path, monkeypatch):
    xlsx = tmp_path / "matches.xlsx"
    _superbet(xlsx)
    state = tmp_path / "state"
    monkeypatch.setattr("superbet.pinnacle.pd.Timestamp.now", lambda tz=None: NOW)
    res = run_daily("2026-09-26", xlsx, state, StakingConfig(), payload=_payload(2.0))
    assert res.compared == 2 and res.unmatched["HomeTeam"].tolist() == ["Steaua"]
    assert res.bets[["home", "selection"]].values.tolist() == [["Arsenal", "1"]]
    assert (state / "value_log.csv").exists() and (state / "pinnacle_snapshots.csv").exists()
    html = email_html(res, "2026-09-26")
    assert "Arsenal" in html and "Cota corectă" in html

    # a later snapshot (closer to kick-off) becomes the closing line for yesterday's bet
    monkeypatch.setattr("superbet.pinnacle.pd.Timestamp.now", lambda tz=None: NOW + pd.Timedelta(hours=20))
    res2 = run_daily("2026-09-26", xlsx, state, StakingConfig(), payload=_payload(1.9))
    assert res2.summary["with_closing_odds"] == 1
    assert res2.summary["clv_mean"] > 0  # 2.30 taken vs 1.90 at the close
    assert "Bilanț" in email_html(res2, "2026-09-26")


def test_email_without_bets():
    from superbet.daily import DailyResult
    html = email_html(DailyResult(pd.DataFrame(), 0, pd.DataFrame(), {}), "2026-09-26")
    assert "Niciun pariu" in html
