import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from superbet.cli import app
from superbet.results import attach_scores, match_scores, parse_score, read_cache, update_results
from superbet.value import settle

TODAY = pd.Timestamp("2026-09-28")


def _log():
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-09-26", "2026-09-26", "2026-09-27", "2026-07-01"]),
        "home": ["Olimpia Elblag", "Tongeren", "Union Molinense", "Old Club"],
        "away": ["Pogon Grodzisk", "RC Hades", "Mazarron", "Other"],
        "market": ["1x2", "ou25", "1x2", "1x2"], "selection": ["2", "Over 2.5", "X", "1"], "book": "SB",
        "odds": [3.05, 1.68, 3.0, 2.0], "fair_p": [0.345, 0.63, 0.37, 0.5], "fair_odds": [2.9, 1.59, 2.7, 2.0],
        "ev": [0.05, 0.05, 0.12, 0.0], "stake": [10.0, 10.0, 10.0, 10.0],
    })


def _day(date):
    return {
        "2026-09-26": pd.DataFrame({"Home Team": ["Olimpia Elbląg", "KSK Tongeren", "Someone"],
                                    "Away Team": ["Pogoń Grodzisk Mazowiecki", "Hades", "Else"],
                                    "Final Score": ["0:2", "2:2", "1:0"]}),
        "2026-09-27": pd.DataFrame({"Home Team": ["Union Molinense"], "Away Team": ["Mazarron FC"],
                                    "Final Score": ["POSTP."]}),
    }.get(date)


def test_parse_score():
    assert parse_score("2:1") == (2.0, 1.0, "finished")
    assert parse_score("POSTP.")[2] == "postponed"
    hg, ag, status = parse_score(np.nan)
    assert np.isnan(hg) and status == "pending"


def test_match_scores_fuzzy_names():
    found = match_scores(_log().iloc[:2], _day("2026-09-26"))
    assert found[["FTHG", "FTAG"]].values.tolist() == [[0, 2], [2, 2]]
    assert found["status"].tolist() == ["finished", "finished"]


def test_update_results_caches_and_keeps_postponed_pending(tmp_path):
    cache_path = tmp_path / "value_results.csv"
    calls = []
    scraper = lambda d, repo: calls.append(d) or _day(d)  # noqa: E731
    cache = update_results(_log(), cache_path, "bet", today=TODAY, scraper=scraper)
    assert sorted(calls) == ["2026-09-26", "2026-09-27"]  # 2026-08-01 is older than the backfill window
    assert cache.set_index("home")["status"].to_dict() == {"Olimpia Elblag": "finished", "Tongeren": "finished",
                                                           "Union Molinense": "postponed"}
    # next run: finished matches are not scraped again, the postponed one is retried
    calls.clear()
    update_results(_log(), cache_path, "bet", today=TODAY, scraper=scraper)
    assert calls == ["2026-09-27"]
    # past the retry window it stays pending without new scrapes
    calls.clear()
    update_results(_log(), cache_path, "bet", today=TODAY + pd.Timedelta(days=30), scraper=scraper)
    assert calls == []

    settled = settle(attach_scores(_log(), read_cache(cache_path)), None)
    s = settled.set_index("home")
    assert s.loc["Olimpia Elblag", "won"] == 1 and s.loc["Olimpia Elblag", "profit_flat"] == pytest.approx(2.05)
    assert s.loc["Olimpia Elblag", "profit"] == pytest.approx(20.5)
    assert s.loc["Tongeren", "won"] == 1 and s.loc["Tongeren", "FTHG"] == 2
    assert s.loc["Union Molinense", "result_status"] == "pending" and np.isnan(s.loc["Union Molinense", "won"])


def test_update_results_without_bet_repo_only_reads_the_cache(tmp_path):
    cache = update_results(_log(), tmp_path / "c.csv", None, today=TODAY,
                           scraper=lambda *a: pytest.fail("must not scrape"))
    assert cache.empty
    assert attach_scores(_log(), cache)["FTHG"].isna().all()


def test_scraper_failure_leaves_bets_pending(tmp_path):
    cache = update_results(_log(), tmp_path / "c.csv", "bet", today=TODAY, scraper=lambda *a: None)
    assert cache.empty


def test_cli_value_settle_with_cached_results_and_filters(tmp_path):
    log = tmp_path / "value_log.csv"
    _log().assign(league=["Poland - 3rd Liga", "Belgium - 2nd Amateur Division", "Spain - Tercera Division",
                          "England - Premier League"]).to_csv(log, index=False)
    update_results(_log(), tmp_path / "value_results.csv", "bet", today=TODAY, scraper=lambda d, r: _day(d))
    history = tmp_path / "snaps.csv"
    pd.DataFrame({
        "taken_at": ["2026-09-26 14:00:00+00:00", "2026-09-26 14:00:00+00:00"],
        "Date": ["26/09/2026", "26/09/2026"], "event_id": [1, 2],
        "starts": ["2026-09-26 15:00:00+00:00", "2026-09-26 14:30:00+00:00"],
        "League": ["Poland - 3rd Liga", "Belgium - 2nd Amateur Division"],
        "HomeTeam": ["Olimpia Elblag", "Tongeren"], "AwayTeam": ["Pogon Grodzisk", "RC Hades"],
        "PSH": [2.1, 2.0], "PSD": [3.5, 3.6], "PSA": [3.2, 3.3], "P>2.5": [1.7, 1.6], "P<2.5": [2.1, 2.3],
    }).to_csv(history, index=False)
    out, summary = tmp_path / "settled.csv", tmp_path / "summary.json"
    r = CliRunner().invoke(app, ["value-settle", "--log", str(log), "--closing-history", str(history),
                                 "--out", str(out), "--summary-out", str(summary)])
    assert r.exit_code == 0, r.output
    assert "eșantion insuficient (<300)" in r.output and "Pe nivel" in r.output
    settled = pd.read_csv(out)
    assert {"FTHG", "FTAG", "won", "profit", "profit_flat", "ev_close", "closing_status", "clv_raw"} <= set(settled)
    assert settled["won"].notna().sum() == 2
    r = CliRunner().invoke(app, ["value-settle", "--log", str(log), "--closing-history", str(history),
                                 "--out", str(out), "--league", "poland"])
    assert r.exit_code == 0 and "Pariuri: 1 " in r.output
    r = CliRunner().invoke(app, ["value-settle", "--log", str(log), "--closing-history", str(history),
                                 "--out", str(out), "--tier", "nope"])
    assert r.exit_code != 0
