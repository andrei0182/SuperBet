#!/bin/bash
# fix_newline.sh — fixes a cosmetic bug: the Over 2.5 summary print had a
# literal "\n" (double-escaped inside the heredoc) instead of an actual
# newline before the summary text.
# Run from the repo root: bash fix_newline.sh
set -e

python3 << 'PYEOF_ANDREI'
path = "main.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '''        print(f"\\\\nOver 2.5 summary: {len(over_odds)}/{len(matches)} matches have an Over 2.5 price, avg odds = {avg_over:.2f}")'''
new = '''        print()
        print(f"Over 2.5 summary: {len(over_odds)}/{len(matches)} matches have an Over 2.5 price, avg odds = {avg_over:.2f}")'''

if old not in content:
    raise SystemExit("ERROR: expected print line not found verbatim — aborting without changes.")
content = content.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched main.py: fixed literal \\\\n in the Over 2.5 summary print.")
PYEOF_ANDREI

echo "Verifying syntax..."
python -m py_compile superbet_scraper/*.py main.py tools/*.py && echo "OK — syntax valid."
