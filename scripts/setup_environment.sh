#!/usr/bin/env bash
set -eu
cd /workspace/kitchen-orders-etl
python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12"'
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir -r requirements.txt
.venv/bin/python -m pip check
