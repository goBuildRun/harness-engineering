#!/usr/bin/env bash
# ael_growth.sh — 从 SUMMARY/PROGRESS/TEST/REVIEW 生成自我成长报告
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

exec python3 "$SCRIPT_DIR/ael_growth.py" \
  --ael-root "$AEL_ROOT" \
  --product-root "$PRODUCT_ROOT" \
  "$@"
