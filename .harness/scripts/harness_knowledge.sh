#!/usr/bin/env bash
# harness_knowledge.sh — 初始化/定位通用产品研发 Harness 知识工件
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

exec python3 "$SCRIPT_DIR/harness_knowledge.py" \
  --harness-root "$HARNESS_ROOT" \
  --product-root "$PRODUCT_ROOT" \
  "$@"
