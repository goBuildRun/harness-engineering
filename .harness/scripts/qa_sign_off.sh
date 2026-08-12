#!/usr/bin/env bash
# qa_sign_off.sh — qa-evaluator 签署验收凭证（含路径审查）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"
EMIT="$SCRIPT_DIR/emit_json.py"
source "$SCRIPT_DIR/harness_output.sh"
WORKSPACE_JSON=$(python3 "$SCRIPT_DIR/workspace_paths.py" --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" json)
AGENT_WS=$(echo "$WORKSPACE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['runs_root'])")

TASK_ID="${1:-}"
DECISION="${2:-}"
SUMMARY="${3:-}"

if [[ -z "$TASK_ID" || -z "$DECISION" ]]; then
  python3 "$EMIT" block "USAGE: qa_sign_off.sh <TASK_ID> pass|fail '<summary>'"
  exit 0
fi

if [[ "$DECISION" != "pass" && "$DECISION" != "fail" ]]; then
  python3 "$EMIT" block "INVALID_DECISION: 仅允许 pass 或 fail"
  exit 0
fi

run_gate() {
  local script="$1"
  shift
  local out
  out=$(bash "$SCRIPT_DIR/$script" "$@" 2>/dev/null) || {
    [[ -n "$out" ]] && harness_print_json "$out"
    return 1
  }
  if echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
    return 0
  fi
  harness_print_json "$out"
  return 1
}

# pass 前强制 structure（--diff）+ plan_sync（无 --diff）
if [[ "$DECISION" == "pass" ]]; then
  if ! run_gate structure_guard.sh --diff; then
    exit 0
  fi
  if ! run_gate plan_sync_check.sh; then
    exit 0
  fi
fi

mkdir -p "$AGENT_WS"
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

ACTIVE_WI=""
if [[ -f "$AGENT_WS/active_task.json" ]]; then
  ACTIVE_WI=$(python3 -c "import json; print(json.load(open('$AGENT_WS/active_task.json')).get('work_item_id') or '')" 2>/dev/null || echo "")
fi

if [[ -n "$ACTIVE_WI" ]]; then
  WS="$AGENT_WS/tasks/$ACTIVE_WI"
  mkdir -p "$WS"
  EVIDENCE="$WS/qa_approved_${TASK_ID}.json"
else
  EVIDENCE="$AGENT_WS/qa_approved_${TASK_ID}.json"
fi
LEGACY_EVIDENCE="$AGENT_WS/qa_approved_${TASK_ID}.json"

BASELINE_FILE=""
if [[ -n "$ACTIVE_WI" ]]; then
  BASELINE_FILE="$AGENT_WS/tasks/$ACTIVE_WI/worktree_baseline.json"
fi
if [[ -n "$BASELINE_FILE" && -f "$BASELINE_FILE" ]]; then
  PATHS_JSON=$(python3 "$SCRIPT_DIR/worktree_baseline.py" changed --repo "$PRODUCT_ROOT" --baseline "$BASELINE_FILE")
else
  PATHS_JSON=$(cd "$PRODUCT_ROOT" && { git diff --name-only 2>/dev/null || true; git diff --cached --name-only 2>/dev/null || true; } | sort -u | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()], ensure_ascii=False))")
fi

python3 "$SCRIPT_DIR/qa_write_evidence.py" "$EVIDENCE" "$TASK_ID" "$DECISION" "$TS" "$SUMMARY" "$PATHS_JSON"
cp "$EVIDENCE" "$LEGACY_EVIDENCE" 2>/dev/null || true

# 同步 05-QA验收.md
PLANNING_GATE="$AGENT_WS/planning_gate_pass.json"
[[ -f "$PLANNING_GATE" ]] || PLANNING_GATE="$AGENT_WS/phase0_pass.json"
if [[ -f "$PLANNING_GATE" ]]; then
  TASK_DIR=$(python3 -c "import json; d=json.load(open('$PLANNING_GATE')); print(d.get('task_dir') or '')" 2>/dev/null)
  if [[ -n "$TASK_DIR" && -f "$TASK_DIR/05-QA验收.md" ]]; then
    {
      echo ""
      echo "## 自动签章 ${TS}"
      echo "- 任务: ${TASK_ID}"
      echo "- 结论: ${DECISION}"
      echo "- 说明: ${SUMMARY}"
      echo "- 凭证: ${EVIDENCE}"
    } >> "$TASK_DIR/05-QA验收.md"
  fi
fi

if [[ "$DECISION" == "fail" ]]; then
  python3 "$EMIT" block "QA_REJECTED: 任务 ${TASK_ID} 未通过。${SUMMARY}"
  exit 0
fi

python3 "$EMIT" pass "QA_SIGNED: 已写入 ${EVIDENCE}（含 paths_reviewed），可 subagent-pr-gate.sh ${TASK_ID}"
