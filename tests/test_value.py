import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from superbet.blend import devig
from superbet.cli import app
from superbet.staking import StakingConfig
from superbet.value import append_log, find_value, join_sources, load_odds_table, load_superbet_excel, settle, \
    summarize, team_key

STAKING = StakingConfig(kelly=0.25, cap=0.02, bankroll=1000)


def _table():
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-03-02", "2024-03-02"]), "home": ["Arsenal", "Leeds"], "away": ["Chelsea", "Hull"],
        "PSH": [2.0, 1.5], "PSD": [3.6, 4.2], "PSA": [4.2, 7.0], "P>2.5": [1.9, 2.0], "P<2.5": [2.0, 1.9],
        "AAH": [2.3, 1.45], "AAD": [3.4, 4.0], "AAA": [4.0, 9.5],
        "BBH": [2.2, 1.5], "BBD": [3.5, 4.0], "BBA": [4.1, 7.0],
        "AA>2.5": [1.95, 2.0], "AA<2.5": [1.9, 1.85],
    })


def test_team_key_is_loose():
    assert team_key("FC Bayern München") == team_key("Bayern Munchen")
    assert team_key("CFR 1907 Cluj") == "cfr 1907 cluj"


def test_find_value_picks_best_book_and_respects_thresholds():
    df = _table()
    bets = find_value(df, ["AA", "BB"], STAKING, ev_min=0.02, max_odds=8.0)
    fair = devig(np.array([[2.0, 3.6, 4.2]]), "power")[0]
    home = bets[(bets["home"] == "Arsenal") & (bets["selection"] == "1")].iloc[0]
    assert home["book"] == "AA" and home["odds"] == 2.3  # best of the two books
    assert home["ev"] == pytest.approx(2.3 * fair[0] - 1)
    assert home["fair_p"] == pytest.approx(fair[0])
    assert home["stake"] == pytest.approx(min(0.25 * (2.3 * fair[0] - 1) / 1.3 * 1000, 20))
    assert (bets["ev"] >= 0.02).all()
    # Leeds away at 9.5 has value but is above max_odds
    assert not ((bets["home"] == "Leeds") & (bets["selection"] == "2")).any()
    assert ((find_value(df, ["AA"], STAKING, 0.02, 10.0)["odds"]) == 9.5).any()


def test_join_sources_matches_loose_names_and_reports_unmatched():
    sharp = _table()[["date", "home", "away", "PSH", "PSD", "PSA"]]
    soft = pd.DataFrame({"Date": ["02/03/2024"] * 3, "HomeTeam": ["Arsenal FC", "Leeds Utd", "Nobody"],
                         "AwayTeam": ["Chelsea", "Hull City", "Else"], "SBH": [2.25, 1.5, 3.0]})
    joined, unmatched = join_sources(sharp, soft, {"Leeds Utd": "Leeds", "Hull City": "Hull"})
    assert joined["SBH"].tolist() == [2.25, 1.5]
    assert unmatched["HomeTeam"].tolist() == ["Nobody"]


def test_join_sources_romanian_countries_fuzzy_names_and_price_check():
    sharp = pd.DataFrame({"date": pd.to_datetime(["2026-09-24"] * 3), "home": ["South Korea", "Amal Tiznit", "Caen"],
                          "away": ["Ecuador", "Ittihad Tanger", "Rouen"], "PSH": [2.3, 3.15, 2.6],
                          "PSD": [3.2, 3.0, 3.2], "PSA": [3.16, 2.36, 2.7]})
    soft = pd.DataFrame({"Date": ["24/09/2026"] * 3,
                         "HomeTeam": ["Coreea de Sud", "US Amal Tiznit", "Caen"],
                         "AwayTeam": ["Ecuador", "Ittihad Riadi Tanger", "Rouen"],
                         "SBH": [2.35, 2.95, 9.0], "SBD": [3.25, 3.1, 5.0], "SBA": [3.15, 2.5, 1.3]})
    joined, unmatched = join_sources(sharp, soft)
    assert joined["SBH"].tolist()[:2] == [2.35, 2.95]  # translated country name, fuzzy club names
    assert np.isnan(joined["SBH"].iloc[2])  # same names but prices far apart: rejected as a wrong match
    assert unmatched["HomeTeam"].tolist() == ["Caen"]
    assert joined["matched_as"].iloc[0] == "Coreea de Sud - Ecuador"


def test_load_superbet_excel(tmp_path):
    path = tmp_path / "matches.xlsx"
    pd.DataFrame({"League": ["X"], "Home Team": ["Arsenal"], "Away Team": ["Chelsea"], "Odds 1": [2.3],
                  "Odds X": [3.4], "Odds 2": [4.0], "O/U Line": [2.5], "Odds Over": [1.9],
                  "Odds Under": [1.95]}).to_excel(path, index=False)
    sb = load_superbet_excel(path, "2024-03-02")
    assert sb.loc[0, "SBH"] == 2.3 and sb.loc[0, "SB>2.5"] == 1.9 and sb.loc[0, "Date"] == "02/03/2024"


def test_settle_and_summarize():
    log = find_value(_table(), ["AA", "BB"], STAKING, 0.02, 8.0)
    results = pd.DataFrame({"date": pd.to_datetime(["2024-03-02", "2024-03-02"]), "home": ["Arsenal", "Leeds"],
                            "away": ["Chelsea", "Hull"], "FTHG": [2, 0], "FTAG": [1, 0],
                            "PSCH": [1.9, 1.5], "PSCD": [3.7, 4.2], "PSCA": [4.5, 7.0],
                            "PC>2.5": [1.8, 2.0], "PC<2.5": [2.1, 1.9]})
    s = settle(log, results)
    home = s[(s["home"] == "Arsenal") & (s["selection"] == "1")].iloc[0]
    assert home["won"] == 1 and home["profit"] == pytest.approx(home["stake"] * 1.3)
    assert home["clv"] == pytest.approx(2.3 / 1.9 - 1)
    assert home["ev_close"] == pytest.approx(2.3 * devig(np.array([[1.9, 3.7, 4.5]]), "power")[0, 0] - 1)
    summary = summarize(s)
    assert summary["settled"] == len(s) and summary["ev_close_mean"] is not None


def test_append_log_keeps_first_price(tmp_path):
    log = tmp_path / "log.csv"
    first = find_value(_table(), ["AA"], STAKING, 0.02, 8.0)
    append_log(first, log)
    later = first.assign(odds=first["odds"] + 0.5)
    merged = append_log(later, log)
    assert len(merged) == len(first)
    assert merged["odds"].tolist() == first["odds"].tolist()


def test_cli_value_flow(tmp_path):
    runner = CliRunner()
    odds = tmp_path / "odds.csv"
    t = _table().drop(columns=["date", "home", "away"])
    t.insert(0, "Date", "02/03/2024")
    t.insert(1, "HomeTeam", ["Arsenal", "Leeds"])
    t.insert(2, "AwayTeam", ["Chelsea", "Hull"])
    t.to_csv(odds, index=False)
    log = tmp_path / "log.csv"
    r = runner.invoke(app, ["value", "--odds", str(odds), "--books", "AA,BB", "--log", str(log)])
    assert r.exit_code == 0, r.output
    assert log.exists()

    results = t.assign(FTHG=[2, 0], FTAG=[1, 0], PSCH=[1.9, 1.5], PSCD=[3.7, 4.2], PSCA=[4.5, 7.0])
    res_path = tmp_path / "results.csv"
    results.to_csv(res_path, index=False)
    r = runner.invoke(app, ["value-settle", "--log", str(log), "--results", str(res_path),
                            "--out", str(tmp_path / "settled.csv")])
    assert r.exit_code == 0, r.output
    assert "EV la închidere" in r.output

    data = tmp_path / "raw"
    data.mkdir()
    results.to_csv(data / "E0_x.csv", index=False)
    r = runner.invoke(app, ["value-backtest", "--data", str(data), "--books", "AA,BB",
                            "--out", str(tmp_path / "vb.csv")])
    assert r.exit_code == 0, r.output
    assert len(load_odds_table(data)) == 2
