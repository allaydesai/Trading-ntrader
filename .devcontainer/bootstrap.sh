#!/usr/bin/env bash
set -e

echo "Setting up Trading-ntrader environment..."

# Create venv
python -m venv .venv
source .venv/bin/activate

# Install dependencies
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
fi

# Optional: install dev tools
pip install pytest black flake8

echo "Setup complete ✅"
