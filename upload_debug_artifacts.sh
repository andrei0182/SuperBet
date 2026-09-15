#!/bin/bash
# upload_debug_artifacts.sh — [SuperBet repo] uploads the bet scraper's
# failure debug files (screenshot + page HTML, saved by
# add_failure_debug_capture.sh) as a downloadable GitHub Actions artifact
# whenever the "Run BetExplorer scraper" step fails, so we can see exactly
# what BetExplorer served to the runner without needing repo access or
# guessing at the cause.
# Run from the SuperBet repo root: bash upload_debug_artifacts.sh
set -e

python3 << 'PYEOF_ANDREI'
path = ".github/workflows/daily-recommendations.yml"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '''      - name: Run BetExplorer scraper (with stats)
        if: env.SKIP_RUN != 'true'
        working-directory: bet
        run: python main.py --date ${{ steps.date.outputs.value }} --with-stats'''

new = '''      - name: Run BetExplorer scraper (with stats)
        if: env.SKIP_RUN != 'true'
        working-directory: bet
        run: python main.py --date ${{ steps.date.outputs.value }} --with-stats

      - name: Upload BetExplorer failure debug files
        if: failure() && env.SKIP_RUN != 'true'
        uses: actions/upload-artifact@v4
        with:
          name: betexplorer-failure-debug
          path: |
            bet/load_date_failure.html
            bet/load_date_failure.png
          if-no-files-found: ignore
          retention-days: 7'''

count = content.count(old)
if count != 1:
    raise SystemExit(f"ERROR: expected exactly 1 occurrence, found {count} — aborting without changes.")
content = content.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched workflow: uploads BetExplorer failure debug files as a downloadable artifact on failure.")
PYEOF_ANDREI
