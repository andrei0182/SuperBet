#!/bin/bash
# fix_dates.sh — fixes fetch_events' date range: the endpoint returned 400
# Bad Request with an end-of-day 23:59:59.999 timestamp for endDate, but
# the confirmed-working curl test used the NEXT calendar day at midnight
# instead. Switches to that format.
# Run from the repo root: bash fix_dates.sh
set -e

python3 << 'PYEOF_ANDREI'
path = "superbet_scraper/events.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '''def _iso_utc(d: dt.date, end_of_day: bool = False) -> str:
    t = "23:59:59.999" if end_of_day else "00:00:00.000"
    return f"{d.isoformat()}T{t}Z"'''
new = '''def _iso_utc(d: dt.date) -> str:
    """Midnight UTC for the given date, in the exact format the endpoint
    expects (confirmed via curl — an end-of-day 23:59:59.999 timestamp for
    endDate returned 400 Bad Request; use the next day's midnight instead
    for an end-exclusive range)."""
    return f"{d.isoformat()}T00:00:00.000Z"'''
if old not in content:
    raise SystemExit("ERROR: _iso_utc not found verbatim — aborting without changes.")
content = content.replace(old, new, 1)

old_call = '''    params = {
        "startDate": _iso_utc(date),
        "endDate": _iso_utc(date, end_of_day=True),'''
new_call = '''    params = {
        "startDate": _iso_utc(date),
        "endDate": _iso_utc(date + dt.timedelta(days=1)),'''
if old_call not in content:
    raise SystemExit("ERROR: fetch_events params block not found verbatim — aborting without changes.")
content = content.replace(old_call, new_call, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched superbet_scraper/events.py: endDate now uses next-day midnight instead of 23:59:59.999.")
PYEOF_ANDREI

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py tools/*.py && echo "OK — syntax valid."
