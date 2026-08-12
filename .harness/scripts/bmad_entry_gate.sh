#!/usr/bin/env bash
# bmad_entry_gate.sh — 兼容入口；语义入口请使用 planning_gate.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
EMIT="$SCRIPT_DIR/emit_json.py"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"
# Preserve the resolved product for nested checks after this script changes cwd.
export HARNESS_PRODUCT_ROOT="$PRODUCT_ROOT"

LEVEL="${1:-}"
TASK_DIR="${2:-}"

if [[ -z "$LEVEL" ]]; then
  python3 "$EMIT" block "USAGE: planning_gate.sh <L1|L2|L3> [harness-workspace/planning/tasks/目录路径]"
  exit 0
fi

if [[ "$LEVEL" != "L1" && "$LEVEL" != "L2" && "$LEVEL" != "L3" ]]; then
  python3 "$EMIT" block "INVALID_LEVEL: 仅允许 L1、L2、L3"
  exit 0
fi

cd "$HARNESS_ROOT"

PLANNING_JSON=$(python3 "$SCRIPT_DIR/workspace_paths.py" --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" json)
PRODUCT_SPECS=$(echo "$PLANNING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['product_specs'])")
AGENT_WS=$(echo "$PLANNING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['agent_workspace'])")

python3 "$SCRIPT_DIR/workspace_paths.py" ensure-dirs --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" >/dev/null

FAILURES=()

# 产品规格
SPEC_COUNT=$(find "$PRODUCT_SPECS" -name '*.md' ! -name 'index.md' 2>/dev/null | wc -l | tr -d ' ')
HAS_CONTEXT=0
[[ -f "$AGENT_WS/context.md" ]] && HAS_CONTEXT=1

if [[ "$SPEC_COUNT" -eq 0 && "$HAS_CONTEXT" -eq 0 ]]; then
  FAILURES+=("NO_PRODUCT_SPEC: 须 harness-workspace/planning/product-specs/<功能>.md 或 harness-workspace/runs/context.md（见产品 harness-workspace/project.yaml）")
fi

# L2/L3 任务目录
TASK_DIR_ABS=""
if [[ "$LEVEL" == "L2" || "$LEVEL" == "L3" ]]; then
  if [[ -z "$TASK_DIR" ]]; then
    FAILURES+=("NO_TASK_DIR: L2/L3 须传入 harness-workspace/planning/tasks/ 目录路径")
  elif [[ ! -d "$TASK_DIR" ]]; then
    FAILURES+=("TASK_DIR_MISSING: $TASK_DIR")
  else
    TASK_DIR_ABS="$(cd "$TASK_DIR" && pwd)"
    require_task_file() {
      [[ -f "$TASK_DIR/$1" ]] || FAILURES+=("MISSING: $TASK_DIR/$1")
    }
    require_task_file "00-任务卡.md"
    require_task_file "03-实施方案.md"
    require_task_file "04-实施记录.md"
    if [[ "$LEVEL" == "L3" ]]; then
      for f in 01-需求与背景.md 02-影响分析.md 05-QA验收.md 06-交付结论.md; do
        require_task_file "$f"
      done
      if [[ -f "$TASK_DIR/02-影响分析.md" ]] && ! grep -qE '契约|API|接口|影响' "$TASK_DIR/02-影响分析.md" 2>/dev/null; then
        FAILURES+=("L3_IMPACT: 02-影响分析.md 须含影响/契约描述")
      fi
    fi
    if [[ -f "$TASK_DIR/00-任务卡.md" ]]; then
      if ! grep -qE 'Gate 1|范围确认' "$TASK_DIR/00-任务卡.md" 2>/dev/null; then
        FAILURES+=("GATE1_DOC: 00-任务卡.md 须含 Gate 1")
      elif ! grep -qE '已确认' "$TASK_DIR/00-任务卡.md" 2>/dev/null; then
        FAILURES+=("GATE1_NOT_CONFIRMED: Gate 1 须标为「已确认」")
      fi
    fi
    if [[ -f "$TASK_DIR/03-实施方案.md" ]]; then
      PLAN_PATHS=$(python3 "$SCRIPT_DIR/business_paths.py" extract --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" < "$TASK_DIR/03-实施方案.md" 2>/dev/null || true)
      if [[ -z "$PLAN_PATHS" ]]; then
        FAILURES+=("PLAN_NO_PATHS: 03-实施方案.md 须登记白名单目标路径（如 services/catalog_service/...）")
      fi
      TASK_CONTRACT=$(bash "$SCRIPT_DIR/task_contract_check.sh" --task-dir "$TASK_DIR_ABS" 2>/dev/null || true)
      if ! echo "$TASK_CONTRACT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
        TASK_REASON=$(echo "$TASK_CONTRACT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('reason','TASK_CONTRACT_FAIL'))" 2>/dev/null || echo "TASK_CONTRACT_FAIL")
        FAILURES+=("$TASK_REASON")
      fi
    fi
  fi
fi

# Work Item 门禁（L2/L3 强制；L1 豁免）
WORK_ITEM_JSON="null"
if [[ "$LEVEL" == "L2" || "$LEVEL" == "L3" ]] && [[ -n "$TASK_DIR_ABS" ]]; then
  WI_GATE=$(python3 "$SCRIPT_DIR/work_item.py" --harness-root "$HARNESS_ROOT" gate --level "$LEVEL" --task-dir "$TASK_DIR_ABS")
  if echo "$WI_GATE" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
    WORK_ITEM_JSON=$(echo "$WI_GATE" | python3 -c "import sys,json; d=json.load(sys.stdin); import json as j; w=d.get('work_item'); print(j.dumps(w) if w else 'null')")
  else
    WI_REASON=$(echo "$WI_GATE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('reason','WORK_ITEM_GATE_FAIL'))" 2>/dev/null || echo "WORK_ITEM_GATE_FAIL")
    FAILURES+=("$WI_REASON")
  fi
fi

if [[ ${#FAILURES[@]} -gt 0 ]]; then
  REASON=$(printf '%s; ' "${FAILURES[@]}")
  python3 "$EMIT" block "BMAD_GATE_BLOCKED: ${REASON} 参见 docs/BMAD_Prelude.md"
  exit 0
fi

BMAD_METHOD=$(python3 "$SCRIPT_DIR/bmad_method_gate.py" --level "$LEVEL" --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" ${TASK_DIR_ABS:+--task-dir "$TASK_DIR_ABS"})
if ! echo "$BMAD_METHOD" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin).get('decision')=='pass' else 1)" 2>/dev/null; then
  BMAD_REASON=$(echo "$BMAD_METHOD" | python3 -c "import sys,json; print(json.load(sys.stdin).get('reason','BMAD_METHOD_GATE_FAIL'))" 2>/dev/null || echo "BMAD_METHOD_GATE_FAIL")
  python3 "$EMIT" block "$BMAD_REASON"
  exit 0
fi

mkdir -p "$AGENT_WS"
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
PLANNING_GATE_FILE="$AGENT_WS/planning_gate_pass.json"
python3 "$SCRIPT_DIR/write_planning_gate.py" "$PLANNING_GATE_FILE" "$LEVEL" "$TS" "$TASK_DIR_ABS" "$PRODUCT_ROOT" "$WORK_ITEM_JSON"

KNOWLEDGE_SYNC=$(python3 "$SCRIPT_DIR/harness_knowledge.py" --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" sync-planning)
if ! echo "$KNOWLEDGE_SYNC" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin).get('decision')=='pass' else 1)" 2>/dev/null; then
  KNOWLEDGE_REASON=$(echo "$KNOWLEDGE_SYNC" | python3 -c "import sys,json; print(json.load(sys.stdin).get('reason','PLANNING_CONTEXT_SYNC_FAIL'))" 2>/dev/null || echo "PLANNING_CONTEXT_SYNC_FAIL")
  python3 "$EMIT" block "$KNOWLEDGE_REASON"
  exit 0
fi
CONTEXT_FILE=$(echo "$KNOWLEDGE_SYNC" | python3 -c "import sys,json; print(json.load(sys.stdin).get('context_file','harness-workspace/knowledge/CONTEXT.md'))" 2>/dev/null || echo "harness-workspace/knowledge/CONTEXT.md")

if [[ "$WORK_ITEM_JSON" != "null" ]]; then
  WI_ID=$(echo "$WORK_ITEM_JSON" | python3 -c "import sys,json; print((json.load(sys.stdin) or {}).get('id') or '')" 2>/dev/null || echo "")
  if [[ -n "$WI_ID" ]]; then
    bash "$SCRIPT_DIR/task_workspace.sh" activate "$WI_ID" >/dev/null 2>&1 || true
  fi
fi

python3 "$EMIT" pass "PLANNING_GATE_OK: 已写入 ${PLANNING_GATE_FILE}（兼容 phase0_pass.json，含 work_item），并同步 ${CONTEXT_FILE}，可 agent_start.sh <work-item-id>"
