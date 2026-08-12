#!/usr/bin/env bash
# release_preflight.sh — 开源发布前防泄漏预检
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

exec python3 "$SCRIPT_DIR/release_preflight.py" --harness-root "$HARNESS_ROOT" "$@"
