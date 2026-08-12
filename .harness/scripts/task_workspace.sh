#!/usr/bin/env bash
# task_workspace.sh — 多任务协作工作区（按 Work Item ID 隔离）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"
EMIT="$SCRIPT_DIR/emit_json.py"

CMD="${1:-}"
shift || true

case "$CMD" in
  activate|show-active|infer-mr|qa-path)
    exec python3 "$SCRIPT_DIR/task_workspace.py" --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" "$CMD" "$@"
    ;;
  "")
    python3 "$EMIT" block "USAGE: task_workspace.sh <activate|show-active|infer-mr|qa-path> ..."
    ;;
  *)
    python3 "$EMIT" block "UNKNOWN_CMD: $CMD"
    ;;
esac
