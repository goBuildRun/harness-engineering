#!/usr/bin/env bash
# dag_sync_check.sh — 校验 tasks-dag.md 与 03-实施方案任务契约同步
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

exec python3 "$SCRIPT_DIR/dag_sync_check.py" \
  --harness-root "$HARNESS_ROOT" \
  --product-root "$PRODUCT_ROOT" \
  "$@"
