#!/bin/bash
# fix_inspect.sh — fixes tools/inspect_page.py: running it directly
# (python tools/inspect_page.py) only puts tools/ on sys.path, not the
# project root, so `from superbet_scraper.driver import build_driver`
# failed to find the sibling package. Adds the project root to sys.path
# explicitly. Also confirms selenium/webdriver-manager are installed.
# Run from the repo root: bash fix_inspect.sh
set -e

pip install -r requirements.txt --break-system-packages 2>/dev/null || pip install -r requirements.txt

python3 << 'PYEOF_ANDREI'
path = "tools/inspect_page.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '''from __future__ import annotations

import argparse
import sys

import requests'''
new = '''from __future__ import annotations

import argparse
import os
import sys

# Ensure the project root (parent of this tools/ directory) is importable,
# regardless of how this script is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests'''

if old not in content:
    raise SystemExit("ERROR: expected import block not found verbatim — aborting without changes.")
content = content.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched tools/inspect_page.py: project root now added to sys.path.")
PYEOF_ANDREI

echo "Verifying syntax..."
python -m py_compile tools/*.py superbet_scraper/*.py && echo "OK — syntax valid."
