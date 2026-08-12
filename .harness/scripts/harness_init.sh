#!/usr/bin/env bash
# harness_init.sh — harness-engineering product onboarding and selection.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/harness_init.py" "$@"
