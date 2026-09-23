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
    assert "taken_at" not in sharp and len(pd.read_csv(hist)) == 4


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
