#!/usr/bin/env bash
# gstack_investigate.sh — 系统化调试：登记假说后才允许继续改代码
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
EMIT="$SCRIPT_DIR/emit_json.py"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"
WORKSPACE_JSON=$(python3 "$SCRIPT_DIR/workspace_paths.py" --harness-root "$ROOT_DIR" --product-root "$PRODUCT_ROOT" json)
AGENT_WS=$(echo "$WORKSPACE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['runs_root'])")

if [[ $# -lt 1 ]]; then
  python3 "$EMIT" block "GSTACK: 须至少提供 1 个技术假说。用法: gstack_investigate.sh '假说1' '假说2'"
  exit 0
fi

LOG="$AGENT_WS/debug-hypotheses.log"
mkdir -p "$(dirname "$LOG")"
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  n=1
  for h in "$@"; do echo "H${n}: $h"; n=$((n+1)); done
} >> "$LOG"

python3 "$EMIT" pass "GSTACK_OK: 已登记 $# 条假说，允许探查与修复"
