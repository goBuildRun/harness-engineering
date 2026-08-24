#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

exec python3 "$SCRIPT_DIR/quality_commands.py" \
  --ael-root "$AEL_ROOT" \
  --product-root "$PRODUCT_ROOT" \
  "$@"
