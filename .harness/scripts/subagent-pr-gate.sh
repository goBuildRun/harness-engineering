#!/usr/bin/env bash
# subagent-pr-gate.sh — Skepticism 交叉验收物理门禁
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
EMIT="$SCRIPT_DIR/emit_json.py"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"
WORKSPACE_JSON=$(python3 "$SCRIPT_DIR/workspace_paths.py" --harness-root "$ROOT_DIR" --product-root "$PRODUCT_ROOT" json)
AGENT_WS=$(echo "$WORKSPACE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['runs_root'])")

TASK_ID="${1:-}"

if [[ -z "$TASK_ID" ]]; then
  python3 "$EMIT" block "ERROR: 必须提供任务编号 (如 T1)"
  exit 0
fi

if ! python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from harness_task_resolution import valid_task_id; sys.exit(0 if valid_task_id(sys.argv[2]) else 1)' "$SCRIPT_DIR" "$TASK_ID"; then
  python3 "$EMIT" block "TASK_ID_INVALID"
  exit 0
fi

QA_RES=$(bash "$SCRIPT_DIR/task_workspace.sh" qa-path "$TASK_ID" 2>/dev/null || true)
QA_EVIDENCE_FILE=$(echo "$QA_RES" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('reason',''))" 2>/dev/null || echo "")
if [[ "$QA_EVIDENCE_FILE" == "TASK_ID_INVALID" || "$QA_EVIDENCE_FILE" == "ACTIVE_TASK_INVALID" ]]; then
  python3 "$EMIT" block "ACTIVE_TASK_ID_INVALID"
  exit 0
fi
if [[ -z "$QA_EVIDENCE_FILE" || ! -f "$QA_EVIDENCE_FILE" ]]; then
  QA_EVIDENCE_FILE="$AGENT_WS/qa_approved_${TASK_ID}.json"
fi

if [[ ! -f "$QA_EVIDENCE_FILE" ]]; then
  python3 "$EMIT" block "VIOLATION_NO_QA: 缺少 ${QA_EVIDENCE_FILE}。请 qa-evaluator 运行: bash .harness/scripts/qa_sign_off.sh ${TASK_ID} pass '<说明>'"
  exit 0
fi

VALID=$(python3 - "$SCRIPT_DIR" "$ROOT_DIR" "$PRODUCT_ROOT" "$QA_EVIDENCE_FILE" "$TASK_ID" <<'PY'
import json, sys
from pathlib import Path

script_dir, harness_root, product_root, path, task_id = sys.argv[1:6]
sys.path.insert(0, script_dir)
from qa_evidence_check import validate_qa_json
from workspace_paths import load_layout
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except (json.JSONDecodeError, OSError) as e:
    print(f"INVALID_JSON:{e}")
    sys.exit(0)
if data.get("decision") != "pass":
    print("NOT_PASSED")
elif data.get("task_id") != task_id:
    print("TASK_MISMATCH")
elif data.get("structure_gate") != "pass":
    print("NO_STRUCTURE_GATE")
elif "paths_reviewed" not in data:
    print("NO_PATHS_REVIEWED")
else:
    work_item_id = str(data.get("work_item_id") or "")
    issues = validate_qa_json(
        Path(path), task_id, work_item_id,
        load_layout(Path(harness_root), Path(product_root)),
    )
    print(issues[0] if issues else "OK")
PY
)

case "$VALID" in
  OK)
    python3 "$EMIT" pass "OK: 交叉验收通过，任务 ${TASK_ID} 可标记为 [x]"
    ;;
  NO_STRUCTURE_GATE)
    python3 "$EMIT" block "QA_NO_STRUCTURE: 凭证缺少 structure_gate pass"
    ;;
  NO_PATHS_REVIEWED)
    python3 "$EMIT" block "QA_NO_PATHS: 凭证缺少 paths_reviewed 字段（可为空数组）"
    ;;
  NOT_PASSED)
    python3 "$EMIT" block "QA_NOT_PASSED: 凭证存在但 decision 非 pass"
    ;;
  TASK_MISMATCH)
    python3 "$EMIT" block "QA_TASK_MISMATCH: 凭证 task_id 与 ${TASK_ID} 不一致"
    ;;
  *)
    python3 "$EMIT" block "QA_INVALID: ${VALID}"
    ;;
esac
