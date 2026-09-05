#!/usr/bin/env bash
# Pipeline tối ưu: backup_credential → fetch → KET_QUA
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=scripts
export GH_TELEMETRY=false

python3 scripts/owned_orders_pipeline.py --pull "$@"
