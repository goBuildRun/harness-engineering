#!/usr/bin/env bash
# Compatibility wrapper; the structured runner is shared by finish and CI.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
exec python3 "$SCRIPT_DIR/ael_gates.py" \
  --ael-root "$AEL_ROOT" \
  --product-root "${AEL_PRODUCT_ROOT:-$(pwd)}" \
  --tier "${AEL_EXECUTION_TIER:-standard}"
