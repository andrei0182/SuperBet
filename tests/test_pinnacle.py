import numpy as np
import pandas as pd
import pytest

from superbet.pinnacle import api_key, attach_closing, closing_lines, events_to_frame, snapshot


def _event(eid, home, away, starts, ml, total=None, league="England - Premier League", parent=None, status="open"):
    game = {"status": status, "money_line": dict(zip(("home", "draw", "away"), ml))}
    if total:
        game["totals"] = {"2.5": {"points": 2.5, "over": total[0], "under": total[1]}}
    return {"event_id": eid, "home": home, "away": away, "league_name": league, "starts": starts,
            "parent_id": parent, "periods": {"num_0": game}}


def _payload(price_home=2.0):
    return {"events": [
        _event(1, "Arsenal", "Chelsea", "2026-09-26T14:00:00Z", (price_home, 3.6, 4.2), (1.9, 2.0)),
        _event(2, "Arsenal (Corners)", "Chelsea (Corners)", "2026-09-26T14:00:00Z", (1.9, 9, 2.0),
               league="England - Premier League Corners"),
        _event(3, "Leeds", "Hull", "2026-09-26T22:30:00Z", (1.5, 4.2, 7.0), parent=None),
        _event(4, "Child", "Event", "2026-09-26T14:00:00Z", (2, 3, 4), parent=1),
        _event(5, "Far", "Away", "2026-10-20T14:00:00Z", (2, 3, 4)),
        _event(6, "Closed", "Market", "2026-09-26T14:00:00Z", (2, 3, 4), status="closed"),
    ]}


NOW = pd.Timestamp("2026-09-25T08:00:00Z")


def test_events_to_frame_filters_and_maps_columns():
    df = events_to_frame(_payload(), NOW)
    assert df["HomeTeam"].tolist() == ["Arsenal", "Leeds", "Far"]
    row = df.iloc[0]
    assert (row["PSH"], row["PSD"], row["PSA"], row["P>2.5"], row["P<2.5"]) == (2.0, 3.6, 4.2, 1.9, 2.0)
    assert np.isnan(df.iloc[1]["P>2.5"])
    assert df.iloc[1]["Date"] == "27/09/2026"  # 22:30 UTC is already the next day in Bucharest


def test_snapshot_writes_table_and_history(tmp_path):
    out, hist = tmp_path / "sharp.csv", tmp_path / "hist.csv"
    snapshot(out, hist, days=3, now=NOW, payload=_payload())
    snapshot(out, hist, days=3, now=NOW + pd.Timedelta(hours=5), payload=_payload(price_home=1.9))
    sharp = pd.read_csv(out)
    assert sharp["HomeTeam"].tolist() == ["Arsenal", "Leeds"]  # "Far" is beyond 3 days
    assert "taken_at" not in sharp
    h = pd.read_csv(hist)  # compacted: one row per match, the latest pre-kick-off snapshot
    assert len(h) == 2 and h.set_index("HomeTeam").loc["Arsenal", "PSH"] == 1.9


def test_snapshot_without_compaction_keeps_every_row(tmp_path):
    hist = tmp_path / "hist.csv"
    for hours in (0, 5):
        snapshot(tmp_path / "a.csv", hist, now=NOW + pd.Timedelta(hours=hours), payload=_payload(), compact=False)
    assert len(pd.read_csv(hist)) == 4


def test_closing_lines_use_last_snapshot_before_kickoff(tmp_path):
    hist = tmp_path / "hist.csv"
    snapshot(tmp_path / "a.csv", hist, now=NOW, payload=_payload(2.0))
    snapshot(tmp_path / "a.csv", hist, now=NOW + pd.Timedelta(hours=5), payload=_payload(1.9))
    # taken after Arsenal kicked off (14:00): must be ignored for Arsenal
    snapshot(tmp_path / "a.csv", hist, now=pd.Timestamp("2026-09-26T15:00:00Z"), payload=_payload(1.5))
    close = closing_lines(hist).set_index("HomeTeam")
    assert close.loc["Arsenal", "PSCH"] == 1.9
    assert close.loc["Leeds", "PSCH"] == 1.5  # Leeds starts 22:30, so the 15:00 snapshot counts
    assert close.loc["Arsenal", "minutes_before_kickoff"] == pytest.approx(25 * 60)


def test_attach_closing_merges_results(tmp_path):
    hist = tmp_path / "hist.csv"
    snapshot(tmp_path / "a.csv", hist, now=NOW, payload=_payload())
    results = pd.DataFrame({"date": pd.to_datetime(["2026-09-26"]), "home": ["Arsenal FC"], "away": ["Chelsea"],
                            "FTHG": [2], "FTAG": [0]})
    res = attach_closing(results, closing_lines(hist)).set_index("home")
    assert res.loc["Arsenal", "FTHG"] == 2 and np.isnan(res.loc["Leeds", "FTHG"])
    assert "PSCH" in res.columns


def test_api_key_required(monkeypatch):
    monkeypatch.delenv("PINNAPI_KEY", raising=False)
    with pytest.raises(RuntimeError):
        api_key()


def test_fetch_prematch_raises_unavailable_on_suspended_account(monkeypatch):
    import io
    import urllib.error

    from superbet.pinnacle import PinnapiUnavailable, fetch_prematch

    def refuse(req, timeout=0):
        body = io.BytesIO(b'{"error":"account_suspended","message":"This account is suspended"}')
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, body)

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    with pytest.raises(PinnapiUnavailable, match="account_suspended"):
        fetch_prematch("key")


def test_cli_snapshot_does_not_fail_when_pinnapi_refuses(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from superbet.cli import app
    from superbet.pinnacle import PinnapiUnavailable

    def refuse(*a, **k):
        raise PinnapiUnavailable("pinnapi HTTP 403 account_suspended")

    monkeypatch.setattr("superbet.pinnacle.snapshot", refuse)
    r = CliRunner().invoke(app, ["pinnacle-snapshot", "--out", str(tmp_path / "s.csv"), "--history", str(tmp_path / "h.csv")])
    assert r.exit_code == 0 and "::warning::" in r.output


def test_request_budget_is_recorded_and_enforced(tmp_path, monkeypatch):
    from superbet.pinnacle import RequestBudgetExhausted, requests_today

    calls = []
    monkeypatch.setattr("superbet.pinnacle.fetch_prematch", lambda key: calls.append(key) or _payload())
    log = tmp_path / "requests.csv"
    for minutes in (0, 15):
        snapshot(tmp_path / "a.csv", tmp_path / "h.csv", now=NOW + pd.Timedelta(minutes=minutes), key="k",
                 requests_log=log, max_per_day=2)
    assert requests_today(log, NOW) == 2 and len(calls) == 2
    with pytest.raises(RequestBudgetExhausted, match="2/2"):
        snapshot(tmp_path / "a.csv", tmp_path / "h.csv", now=NOW + pd.Timedelta(hours=1), key="k",
                 requests_log=log, max_per_day=2)
    assert len(calls) == 2  # refused before any request
    # the budget is per UTC day; a payload given directly (tests, replays) is not a request
    assert requests_today(log, NOW + pd.Timedelta(days=1)) == 0
    snapshot(tmp_path / "a.csv", tmp_path / "h.csv", now=NOW + pd.Timedelta(hours=2), payload=_payload(),
             requests_log=log, max_per_day=2)
    assert requests_today(log, NOW) == 2


def test_kickoff_within_reads_the_history(tmp_path):
    from superbet.pinnacle import kickoff_within

    hist = tmp_path / "h.csv"
    assert kickoff_within(hist, NOW, 180)  # no history yet: fetch
    snapshot(tmp_path / "a.csv", hist, now=NOW, payload=_payload())  # Arsenal 14:00Z, Leeds 22:30Z on 26.09
    assert not kickoff_within(hist, pd.Timestamp("2026-09-26T08:00:00Z"), 180)
    assert kickoff_within(hist, pd.Timestamp("2026-09-26T11:30:00Z"), 180)
    assert not kickoff_within(hist, pd.Timestamp("2026-09-26T14:30:00Z"), 180)  # Arsenal started, Leeds is 8h away


def test_cli_snapshot_gate_skips_without_a_request(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from superbet.cli import app

    hist = tmp_path / "h.csv"
    snapshot(tmp_path / "a.csv", hist, now=NOW, payload=_payload())
    monkeypatch.setattr("superbet.pinnacle.fetch_prematch", lambda key: pytest.fail("no request expected"))
    monkeypatch.setattr("superbet.cli.pd.Timestamp.now", lambda tz=None: pd.Timestamp("2026-09-26T08:00:00Z"))
    r = CliRunner().invoke(app, ["pinnacle-snapshot", "--out", str(tmp_path / "s.csv"), "--history", str(hist),
                                 "--only-if-kickoff-within", "180"])
    assert r.exit_code == 0, r.output
    assert "captură sărită" in r.output
