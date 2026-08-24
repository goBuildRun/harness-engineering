#!/usr/bin/env bash
# ael_init.sh — buildrun-agent-engineering-lifecycle product onboarding and selection.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/ael_init.py" "$@"
