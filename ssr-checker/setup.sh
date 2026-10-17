#!/usr/bin/env bash
# LOCAL convenience script only.
#
# Streamlit Community Cloud does NOT run this file -- it installs
# `requirements.txt` (pip) and `packages.txt` (apt) automatically, and the app
# downloads the Chromium binary itself on first run (see ensure_chromium()).
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium

echo "Done. Start the app with: streamlit run app.py"
