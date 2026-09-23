import numpy as np
import pandas as pd

from superbet.data import attach_btts, load_btts, load_matches, normalize_team, parse_dates


def _write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def test_normalize_team():
    assert normalize_team("  Man  United ") == "Man United"


def test_parse_dates_both_formats():
    out = parse_dates(pd.Series(["12/08/23", "12/08/2023"]))
    assert (out == pd.Timestamp("2023-08-12")).all()


def test_load_matches_cleans_and_sorts(tmp_path):
    _write_csv(tmp_path / "E0_2324.csv", [
        {"Div": "E0", "Date": "20/08/2023", "HomeTeam": "B", "AwayTeam": "A", "FTHG": 1, "FTAG": 1,
         "B365H": 2.0, "B365D": 3.4, "B365A": 3.8, "B365>2.5": 1.9, "B365<2.5": 1.95,
         "PSCH": 2.05, "PSCD": 3.3, "PSCA": 3.9},
        {"Div": "E0", "Date": "12/08/23", "HomeTeam": " A ", "AwayTeam": "B", "FTHG": 2, "FTAG": 0,
         "B365H": np.nan, "AvgH": 1.8, "B365D": 3.5, "B365A": 4.5},
        {"Div": "E0", "Date": "27/08/2023", "HomeTeam": "A", "AwayTeam": "C", "FTHG": np.nan, "FTAG": np.nan},
    ])
    _write_csv(tmp_path / "btts_odds.csv", [
        {"Date": "20/08/2023", "HomeTeam": "B", "AwayTeam": "A", "gg_yes": 1.7, "gg_no": 2.1},
    ])
    df = load_matches(tmp_path)
    assert len(df) == 2  # row without score dropped, btts file not treated as matches
    assert df["date"].is_monotonic_increasing
    assert df.iloc[0]["home"] == "A"
    assert df.iloc[0]["odds_h"] == 1.8  # fallback to Avg when B365 missing
    assert df.iloc[1]["close_h"] == 2.05
    assert np.isnan(df.iloc[1]["close_over"])  # column absent in file

    merged = attach_btts(df, load_btts(tmp_path / "btts_odds.csv"))
    assert merged.iloc[1]["odds_gg"] == 1.7
    assert np.isnan(merged.iloc[0]["odds_gg"])


def test_load_btts_missing_file(tmp_path):
    assert load_btts(tmp_path / "nope.csv") is None
