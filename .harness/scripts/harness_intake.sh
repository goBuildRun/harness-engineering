#!/usr/bin/env bash
# harness_intake.sh — 扫描已有项目代码/文档，生成接入候选报告
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

exec python3 "$SCRIPT_DIR/harness_intake.py" \
  --harness-root "$HARNESS_ROOT" \
  --product-root "$PRODUCT_ROOT" \
  "$@"
