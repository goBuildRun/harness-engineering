#!/usr/bin/env bash
# mr_ready.sh — product-root MR readiness gate
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

exec python3 "$SCRIPT_DIR/mr_ready.py" \
  --ael-root "$AEL_ROOT" \
  --product-root "$PRODUCT_ROOT" \
  "$@"
