#!/bin/bash
# set_schedule_4am.sh — [SuperBet repo] schedules the daily workflow for
# 4:00 AM Romania time, year-round. GitHub Actions cron is always UTC and
# has no timezone concept, so a single fixed cron entry would drift by an
# hour every time Romania's DST changes (EEST/UTC+3 in summer, EET/UTC+2
# in winter). Fix: two cron triggers (one per offset) PLUS a guard step
# that checks the actual current Europe/Bucharest hour and skips the run
# if it is not really 4 AM local (covers the ~1 week/year where the two
# entries would otherwise both fire, or neither would, right around the
# DST switch date itself).
# Run from the SuperBet repo root: bash set_schedule_4am.sh
set -e

python3 << 'PYEOF_ANDREI'
path = ".github/workflows/daily-recommendations.yml"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_on = (
    "on:\n"
    "  schedule:\n"
    "    # 07:00 UTC — adjust to your preferred send time. Cron is always UTC;\n"
    "    # this is ~09:00/10:00 Romania time depending on DST.\n"
    "    - cron: \"0 7 * * *\"\n"
    "  workflow_dispatch: {}  # lets you trigger it manually from the Actions tab to test"
)

new_on = (
    "on:\n"
    "  schedule:\n"
    "    # 4:00 AM Romania time, year-round. Cron is always UTC with no DST\n"
    "    # concept, so two entries cover both of Romania's UTC offsets — the\n"
    "    # guard step below then skips whichever one does not actually land on\n"
    "    # 4 AM local (handles the DST-switch week where both/neither would\n"
    "    # otherwise be exactly right).\n"
    "    - cron: \"0 1 * * *\"  # 4:00 Romania time when EEST (UTC+3, summer)\n"
    "    - cron: \"0 2 * * *\"  # 4:00 Romania time when EET (UTC+2, winter)\n"
    "  workflow_dispatch: {}  # lets you trigger it manually from the Actions tab to test"
)

if old_on not in content:
    raise SystemExit("ERROR: expected on/schedule block not found verbatim — aborting without changes.")
content = content.replace(old_on, new_on, 1)

old_steps_start = (
    "jobs:\n"
    "  send-recommendations:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: Checkout SuperBet\n"
)

guard_step = (
    "jobs:\n"
    "  send-recommendations:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: Skip unless this is actually 4 AM Romania time\n"
    "        run: |\n"
    "          hour=$(TZ=\"Europe/Bucharest\" date +%H)\n"
    "          echo \"Current Romania local hour: $hour\"\n"
    "          if [ \"$hour\" != \"04\" ]; then\n"
    "            echo \"Not 4 AM Romania time right now (this is the other cron entry, for the other DST offset) -- skipping the rest of the job.\"\n"
    "            echo \"SKIP_RUN=true\" >> \"$GITHUB_ENV\"\n"
    "          fi\n"
    "\n"
    "      - name: Checkout SuperBet\n"
)

if old_steps_start not in content:
    raise SystemExit("ERROR: expected jobs/steps block not found verbatim — aborting without changes.")
content = content.replace(old_steps_start, guard_step, 1)

step_names = [
    "Checkout SuperBet",
    "Checkout BetExplorer",
    "Set up Python",
    "Install BetExplorer dependencies",
    "Install SuperBet dependencies",
    "Compute today's date",
    "Run BetExplorer scraper (with stats)",
    "Run SuperBet scraper (all leagues)",
    "Send recommendation email",
]
for name in step_names:
    old_step_header = "      - name: " + name + "\n"
    if old_step_header not in content:
        raise SystemExit("ERROR: step '" + name + "' not found verbatim — aborting without changes.")
    new_step_header = "      - name: " + name + "\n        if: env.SKIP_RUN != 'true'\n"
    content = content.replace(old_step_header, new_step_header, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched .github/workflows/daily-recommendations.yml: runs at 4 AM Romania time year-round (DST-safe).")
PYEOF_ANDREI
