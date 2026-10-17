#!/usr/bin/env bash
# Streamlit Community Cloud build hook: install the Chromium browser binary
# that Playwright needs. The OS libraries come from packages.txt.
set -euo pipefail

pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
