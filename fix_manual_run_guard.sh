#!/bin/bash
# fix_manual_run_guard.sh — [SuperBet repo] makes the "is it 4 AM Romania
# time" guard apply ONLY to the automatic schedule triggers (which is what
# it's for -- picking the right one of the two DST cron entries). A manual
# "Run workflow" click (workflow_dispatch) always runs the full job now,
# regardless of the current time, so you can test on demand.
# Run from the SuperBet repo root: bash fix_manual_run_guard.sh
set -e

python3 << 'PYEOF_ANDREI'
path = ".github/workflows/daily-recommendations.yml"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '''      - name: Skip unless this is actually 4 AM Romania time
        run: |
          hour=$(TZ="Europe/Bucharest" date +%H)
          echo "Current Romania local hour: $hour"
          if [ "$hour" != "04" ]; then
            echo "Not 4 AM Romania time right now (this is the other cron entry, for the other DST offset) -- skipping the rest of the job."
            echo "SKIP_RUN=true" >> "$GITHUB_ENV"
          fi'''

new = '''      - name: Skip unless this is actually 4 AM Romania time
        run: |
          if [ "${{ github.event_name }}" != "schedule" ]; then
            echo "Manually triggered (workflow_dispatch) -- running regardless of current time."
          else
            hour=$(TZ="Europe/Bucharest" date +%H)
            echo "Current Romania local hour: $hour"
            if [ "$hour" != "04" ]; then
              echo "Not 4 AM Romania time right now (this is the other cron entry, for the other DST offset) -- skipping the rest of the job."
              echo "SKIP_RUN=true" >> "$GITHUB_ENV"
            fi
          fi'''

count = content.count(old)
if count != 1:
    raise SystemExit(f"ERROR: expected exactly 1 occurrence, found {count} — aborting without changes.")
content = content.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched workflow: the 4 AM guard now only applies to scheduled runs, not manual ones.")
PYEOF_ANDREI
