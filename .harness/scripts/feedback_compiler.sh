#!/usr/bin/env bash
# feedback_compiler.sh — 后置反馈沙箱（与 run_in_sandbox 同语义，保留兼容）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/run_in_sandbox.sh" "$@"
