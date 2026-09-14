#!/bin/bash
# fix_timezone.sh — [SuperBet repo] fixes the kickoff time shown in the
# daily email: both scraped sources store the RAW UTC time (confirmed via
# diagnose_times.py — BetExplorer "17:00" == Superbet "2026-09-14
# 17:00:00", while the Superbet app itself displays the same match at
# 20:00 local). Converts Superbet's raw UTC datetime to Europe/Bucharest
# using zoneinfo (correctly DST-aware, so it stays right after the
# October clock change too) and uses that as the email's kickoff time,
# instead of BetExplorer's unconverted, date-less time string.
# Run from the SuperBet repo root: bash fix_timezone.sh
set -e

python3 << 'PYEOF_ANDREI'
path = "daily_recommendations.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1) Add the imports and a conversion helper.
old_imports = '''import argparse
import os
import smtplib
import sys
import unicodedata
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd

_SUFFIX_BET = " (BetExplorer)"
_SUFFIX_SB = " (Superbet)"'''

new_imports = '''import argparse
import os
import smtplib
import sys
import unicodedata
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import pandas as pd

_SUFFIX_BET = " (BetExplorer)"
_SUFFIX_SB = " (Superbet)"
_LOCAL_TZ = ZoneInfo("Europe/Bucharest")


def to_local_kickoff(raw) -> str:
    """Both BetExplorer and Superbet store the kickoff time in UTC (CONFIRMED
    2026-09-14 via diagnose_times.py: BetExplorer's "17:00" matches
    Superbet's raw "2026-09-14 17:00:00" exactly, while the Superbet app
    itself displays that same match at 20:00 local). Superbet is the only
    one of the two that exports a full date+time (BetExplorer only exports
    a bare "HH:MM"), so it is the only one this can convert unambiguously.
    Uses zoneinfo (not a fixed +3h offset) so this stays correct across the
    October/March DST changes, not just for today.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    try:
        parsed = pd.to_datetime(raw, utc=True)
    except (ValueError, TypeError):
        return str(raw)
    return parsed.tz_convert(_LOCAL_TZ).strftime("%H:%M")'''

if old_imports not in content:
    raise SystemExit("ERROR: expected imports block not found verbatim — aborting without changes.")
content = content.replace(old_imports, new_imports, 1)

# 2) Compute the local kickoff column when loading Superbet's sheet.
old_load_sb = '''def load_superbet_matches(superbet_xlsx: str) -> pd.DataFrame:
    df = pd.read_excel(superbet_xlsx, sheet_name="Matches")
    df["_home_norm"] = df["Home Team"].map(normalize_name)
    df["_away_norm"] = df["Away Team"].map(normalize_name)
    return df'''

new_load_sb = '''def load_superbet_matches(superbet_xlsx: str) -> pd.DataFrame:
    df = pd.read_excel(superbet_xlsx, sheet_name="Matches")
    df["_home_norm"] = df["Home Team"].map(normalize_name)
    df["_away_norm"] = df["Away Team"].map(normalize_name)
    df["_kickoff_local"] = df["Kick-off Time"].map(to_local_kickoff)
    return df'''

if old_load_sb not in content:
    raise SystemExit("ERROR: expected load_superbet_matches function not found verbatim — aborting without changes.")
content = content.replace(old_load_sb, new_load_sb, 1)

# 3) Use the converted local time in the email instead of BetExplorer's raw time.
old_time_line = '            time_text = row.get("Kick-off Time" + _SUFFIX_BET, "")'
new_time_line = '            time_text = row.get("_kickoff_local" + _SUFFIX_SB, "") + " (ora Romaniei)"'

if old_time_line not in content:
    raise SystemExit("ERROR: expected time_text line not found verbatim — aborting without changes.")
content = content.replace(old_time_line, new_time_line, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched daily_recommendations.py: email now shows the DST-aware Romania local kickoff time.")
PYEOF_ANDREI

echo "Verifying syntax..."
python3 -c "import ast; ast.parse(open('daily_recommendations.py').read())" && echo "OK -- syntax valid."
