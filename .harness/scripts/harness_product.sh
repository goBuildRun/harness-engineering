#!/usr/bin/env bash
# harness_product.sh — resolve or pin product context without changing active-product.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/harness_product.py" "$@"
