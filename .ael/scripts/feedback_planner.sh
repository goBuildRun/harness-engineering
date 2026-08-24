#!/usr/bin/env bash
# feedback_planner.sh — TDD + DAG + 路径计划门禁
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
EMIT="$SCRIPT_DIR/emit_json.py"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

PLAN_TEXT="${1:-}"

if [[ -z "$PLAN_TEXT" ]]; then
  python3 "$EMIT" block "ERROR_NO_INPUT: 必须传入任务切分计划"
  exit 0
fi

if [[ ! "$PLAN_TEXT" =~ test|测试|TDD ]]; then
  python3 "$EMIT" block "VIOLATION_NO_TDD: 计划须包含测试/TDD 步骤"
  exit 0
fi

if [[ ! "$PLAN_TEXT" =~ \[ ]]; then
  python3 "$EMIT" block "VIOLATION_NO_DAG: 计划须使用 [ ] / [x] 勾选语法"
  exit 0
fi

PLAN_PATHS=$(printf '%s\n' "$PLAN_TEXT" | python3 "$SCRIPT_DIR/business_paths.py" extract --ael-root "$AEL_ROOT" --product-root "$PRODUCT_ROOT" 2>/dev/null || true)

# 须含业务路径或 structure_guard
if [[ -z "$PLAN_PATHS" && ! "$PLAN_TEXT" =~ structure_guard ]]; then
  ROOTS=$(python3 "$SCRIPT_DIR/business_paths.py" roots --ael-root "$AEL_ROOT" --product-root "$PRODUCT_ROOT" 2>/dev/null | tr '\n' ' ' || true)
  python3 "$EMIT" block "VIOLATION_NO_PATH: 计划须含 active profile 允许的目标路径或 structure_guard；允许根: ${ROOTS}"
  exit 0
fi

# L2+ 须提及实施方案
WORKSPACE_JSON=$(python3 "$SCRIPT_DIR/workspace_paths.py" --ael-root "$AEL_ROOT" --product-root "$PRODUCT_ROOT" json)
AGENT_WS=$(echo "$WORKSPACE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['runs_root'])")
PLANNING_GATE="$AGENT_WS/planning_gate_pass.json"
[[ -f "$PLANNING_GATE" ]] || PLANNING_GATE="$AGENT_WS/phase0_pass.json"
if [[ -f "$PLANNING_GATE" ]]; then
  LEVEL=$(python3 -c "import json; print(json.load(open('$PLANNING_GATE')).get('level',''))" 2>/dev/null || echo "")
  if [[ "$LEVEL" == "L2" || "$LEVEL" == "L3" ]]; then
    if [[ ! "$PLAN_TEXT" =~ 03-实施方案|实施方案|T[0-9] ]]; then
      python3 "$EMIT" block "VIOLATION_NO_PLAN_REF: L2/L3 计划须引用 03-实施方案 任务 ID (如 T1)"
      exit 0
    fi
  fi
fi

# 预检计划中的路径
while IFS= read -r p; do
  [[ -z "$p" ]] && continue
  RESULT=$(bash "$SCRIPT_DIR/structure_guard.sh" --path "$p" 2>/dev/null || true)
  if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
    :
  else
    python3 "$EMIT" block "PATH_PREFLIGHT_FAIL: $p — $RESULT"
    exit 0
  fi
done <<< "$PLAN_PATHS"

python3 "$EMIT" pass "OK: TDD、DAG、路径与计划引用检查通过"
