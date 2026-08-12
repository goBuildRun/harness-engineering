#!/usr/bin/env bash
# Compatibility wrapper; the structured runner is shared by finish and CI.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
exec python3 "$SCRIPT_DIR/harness_gates.py" \
  --harness-root "$HARNESS_ROOT" \
  --product-root "${HARNESS_PRODUCT_ROOT:-$(pwd)}" \
  --tier "${HARNESS_EXECUTION_TIER:-standard}"
