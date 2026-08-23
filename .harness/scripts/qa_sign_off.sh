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

if ! python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from harness_task_resolution import valid_task_id; sys.exit(0 if valid_task_id(sys.argv[2]) else 1)' "$SCRIPT_DIR" "$TASK_ID"; then
  python3 "$EMIT" block "TASK_ID_INVALID"
  exit 0
fi

if [[ "$DECISION" != "pass" && "$DECISION" != "fail" ]]; then
  python3 "$EMIT" block "INVALID_DECISION: 仅允许 pass 或 fail"
  exit 0
fi

mkdir -p "$AGENT_WS"
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

ACTIVE_WI=""
if [[ -f "$AGENT_WS/active_task.json" ]]; then
  if ! ACTIVE_WI=$(python3 - "$AGENT_WS/active_task.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
if not isinstance(data, dict):
    raise SystemExit(1)
value = str(data.get("work_item_id") or data.get("task_id") or "").strip()
if not value:
    raise SystemExit(1)
print(value)
PY
  ); then
    python3 "$EMIT" block "ACTIVE_TASK_INVALID"
    exit 0
  fi
fi

if [[ -n "$ACTIVE_WI" ]] && ! python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from harness_task_resolution import valid_task_id; sys.exit(0 if valid_task_id(sys.argv[2]) else 1)' "$SCRIPT_DIR" "$ACTIVE_WI"; then
  python3 "$EMIT" block "ACTIVE_TASK_ID_INVALID"
  exit 0
fi

if [[ -n "$ACTIVE_WI" ]]; then
  WS="$AGENT_WS/tasks/$ACTIVE_WI"
  mkdir -p "$WS"
  EVIDENCE="$WS/qa_approved_${TASK_ID}.json"
else
  EVIDENCE="$AGENT_WS/qa_approved_${TASK_ID}.json"
fi
BASELINE_FILE=""
if [[ -n "$ACTIVE_WI" ]]; then
  BASELINE_FILE="$AGENT_WS/tasks/$ACTIVE_WI/worktree_baseline.json"
fi
if [[ -n "$BASELINE_FILE" && -f "$BASELINE_FILE" ]]; then
  PATHS_JSON=$(python3 "$SCRIPT_DIR/worktree_baseline.py" changed --repo "$PRODUCT_ROOT" --baseline "$BASELINE_FILE")
else
  PATHS_JSON=$(cd "$PRODUCT_ROOT" && { git diff --name-only 2>/dev/null || true; git diff --cached --name-only 2>/dev/null || true; } | sort -u | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()], ensure_ascii=False))")
fi

BINDING_JSON="{}"
if [[ "$DECISION" == "pass" ]]; then
  BUNDLE_STATUS=$(python3 "$SCRIPT_DIR/qa_evidence_binding.py" status \
    --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" \
    --work-item "$ACTIVE_WI" --paths-json "$PATHS_JSON" || true)
  if ! echo "$BUNDLE_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
    BUNDLE_STATUS=$(python3 "$SCRIPT_DIR/qa_evidence_binding.py" prepare \
      --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" \
      --work-item "$ACTIVE_WI" --paths-json "$PATHS_JSON" || true)
    if ! echo "$BUNDLE_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
      harness_print_json "$BUNDLE_STATUS"
      exit 0
    fi
  fi
  RECEIPT_BINDING=$(python3 "$SCRIPT_DIR/qa_evidence_binding.py" binding \
    --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" \
    --work-item "$ACTIVE_WI" --task-id "$TASK_ID" --paths-json "$PATHS_JSON" || true)
  if ! echo "$RECEIPT_BINDING" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
    harness_print_json "$RECEIPT_BINDING"
    exit 0
  fi
  BINDING_JSON=$(echo "$RECEIPT_BINDING" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['binding'], ensure_ascii=False))")
  CANDIDATE_SNAPSHOT="$AGENT_WS/tasks/$ACTIVE_WI/candidate-snapshot.json"
  if [[ -f "$CANDIDATE_SNAPSHOT" ]]; then
    if ! BINDING_JSON=$(python3 - "$BINDING_JSON" "$CANDIDATE_SNAPSHOT" <<'PY'
import json
import sys

binding = json.loads(sys.argv[1])
snapshot = json.loads(open(sys.argv[2], encoding="utf-8").read())
paths = snapshot.get("candidate_paths")
subject = str(snapshot.get("candidate_digest") or "")
digest = str(snapshot.get("snapshot_digest") or "")
if not isinstance(binding, dict) or not isinstance(paths, list) or not subject or not digest:
    raise SystemExit(1)
binding.update({
    "candidate_paths_reviewed": paths,
    "candidate_subject_digest": subject,
    "candidate_snapshot_digest": digest,
})
print(json.dumps(binding, ensure_ascii=False))
PY
    ); then
      python3 "$EMIT" block "QA_CANDIDATE_BINDING_INVALID"
      exit 0
    fi
  fi
fi

python3 "$SCRIPT_DIR/qa_write_evidence.py" "$EVIDENCE" "$TASK_ID" "$ACTIVE_WI" "$DECISION" "$TS" "$SUMMARY" "$PATHS_JSON" "$BINDING_JSON"

if [[ "$DECISION" == "fail" ]]; then
  python3 "$EMIT" block "QA_REJECTED: 任务 ${TASK_ID} 未通过。${SUMMARY}"
  exit 0
fi

python3 "$EMIT" pass "QA_SIGNED: 已写入 ${EVIDENCE}（含 paths_reviewed），可 subagent-pr-gate.sh ${TASK_ID}"
