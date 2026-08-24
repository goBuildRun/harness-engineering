#!/usr/bin/env bash
# run_in_sandbox.sh — 受控命令入口与 JSON 反馈；支持 Docker backend
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

if [[ $# -eq 0 ]]; then
  python3 "$SCRIPT_DIR/emit_json.py" block "ERROR: 必须传入要在沙箱中执行的命令"
  exit 1
fi

exec python3 "$SCRIPT_DIR/sandbox_exec.py" \
  --cwd "$PRODUCT_ROOT" \
  "$@"
