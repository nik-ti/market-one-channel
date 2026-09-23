#!/usr/bin/env bash
# Starts the dashboard API with uvicorn. Used locally and by the systemd
# service described in SPEC.md.
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
