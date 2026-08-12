#!/usr/bin/env bash
# qa_evidence_check.sh — verify QA sign-off plus TEST/REVIEW reports.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

exec python3 "$SCRIPT_DIR/qa_evidence_check.py" --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" "$@"
