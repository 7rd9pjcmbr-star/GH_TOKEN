#!/usr/bin/env bash
# Đọc credential → lấy đơn → KET_QUA (một lệnh)
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=scripts
export GH_TELEMETRY=false

python3 scripts/credential_fetch_orders.py --pull "$@"

rows=0
f="reports/telegram-classify/KET_QUA_DON_CHIET_TIET.csv"
[[ -f "$f" ]] && rows=$(( $(wc -l < "$f") - 1 ))
echo "KET_QUA rows=$rows file=$f"
