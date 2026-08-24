#!/usr/bin/env bash
# task_contract_check.sh — 校验 03-实施方案的 AEL 7 字段任务契约
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

exec python3 "$SCRIPT_DIR/task_contract_check.py" \
  --ael-root "$AEL_ROOT" \
  --product-root "$PRODUCT_ROOT" \
  "$@"
