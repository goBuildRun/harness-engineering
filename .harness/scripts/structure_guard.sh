#!/usr/bin/env bash
# structure_guard.sh — 程序包/路径结构机械门禁
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

ARGS=()
if [[ $# -eq 0 ]]; then
  ARGS=(--diff)
else
  ARGS=("$@")
fi

python3 "$SCRIPT_DIR/structure_check.py" \
  --harness-root "$HARNESS_ROOT" \
  --product-root "$PRODUCT_ROOT" \
  "${ARGS[@]}"
