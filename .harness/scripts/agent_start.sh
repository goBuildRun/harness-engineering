#!/usr/bin/env bash
# agent_start.sh — Harness Execution 启动（Work Item 驱动，自动恢复 planning gate）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
EMIT="$SCRIPT_DIR/emit_json.py"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"
source "$SCRIPT_DIR/harness_output.sh"

if [[ -z "${1:-}" ]]; then
  python3 "$EMIT" block "USAGE: agent_start.sh <WORK_ITEM_ID>  # 当前产品 Work Item provider 的任务 ID"
  exit 0
fi

WORK_ITEM_ID="$1"

if ! python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from harness_task_resolution import valid_task_id; sys.exit(0 if valid_task_id(sys.argv[2]) else 1)' "$SCRIPT_DIR" "$WORK_ITEM_ID"; then
  python3 "$EMIT" block "TASK_ID_INVALID"
  exit 0
fi

WORKSPACE_JSON=$(python3 "$SCRIPT_DIR/workspace_paths.py" --harness-root "$HARNESS_ROOT" --product-root "$PRODUCT_ROOT" json)
AGENT_WS=$(echo "$WORKSPACE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['agent_workspace'])")
TASKS_ROOT=$(echo "$WORKSPACE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['tasks'])")

ACTIVATE=$(bash "$SCRIPT_DIR/task_workspace.sh" activate "$WORK_ITEM_ID" 2>/dev/null || true)
if ! echo "$ACTIVATE" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
  if [[ ! -f "$AGENT_WS/planning_gate_pass.json" && ! -f "$AGENT_WS/phase0_pass.json" ]]; then
    harness_print_json "$ACTIVATE"
    exit 0
  fi
fi

GATE_FILE="$AGENT_WS/planning_gate_pass.json"
[[ -f "$GATE_FILE" ]] || GATE_FILE="$AGENT_WS/phase0_pass.json"
VALID=$(python3 - "$GATE_FILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    d = json.load(f)
print("OK" if d.get("decision") == "pass" else "BAD")
PY
)
if [[ "$VALID" != "OK" ]]; then
  python3 "$EMIT" block "PLANNING_GATE_INVALID: 重新执行 planning_gate 或确认 harness-workspace/planning/tasks/ 含 planning_gate_pass.json（兼容 phase0_pass.json）"
  exit 0
fi

LEVEL=$(python3 -c "import json; print(json.load(open('$GATE_FILE'))['level'])")
TASK_DIR=$(python3 -c "import json; d=json.load(open('$GATE_FILE')); print(d.get('task_dir') or 'N/A')")
BOUND_ID=$(python3 -c "import json; d=json.load(open('$GATE_FILE')); w=d.get('work_item') or {}; print(w.get('id') or '' if isinstance(w, dict) else '')")
GATE_PROVIDER=$(python3 -c "import json; d=json.load(open('$GATE_FILE')); w=d.get('work_item') or {}; print(w.get('provider') or 'noop' if isinstance(w, dict) else 'noop')")
if [[ -z "$BOUND_ID" ]]; then
  python3 "$EMIT" block "NO_WORK_ITEM_IN_PLANNING_GATE: 重新 planning_gate"
  exit 0
fi
if [[ "$WORK_ITEM_ID" != "$BOUND_ID" ]]; then
  python3 "$EMIT" block "WORK_ITEM_MISMATCH: 参数 ${WORK_ITEM_ID} 与 planning gate ${BOUND_ID} 不一致"
  exit 0
fi

LIFECYCLE_PREFLIGHT=$(python3 "$SCRIPT_DIR/harness_lifecycle_preflight.py" \
  --product-root "$PRODUCT_ROOT" --provider "$GATE_PROVIDER" 2>/dev/null || true)
if [[ "$LEVEL" == "L3" ]] && ! echo "$LIFECYCLE_PREFLIGHT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
  harness_print_json "$LIFECYCLE_PREFLIGHT"
  exit 0
fi

if [[ "$LEVEL" == "L2" || "$LEVEL" == "L3" ]]; then
  WI_VERIFY=$(bash "$SCRIPT_DIR/work_item.sh" verify --id "$WORK_ITEM_ID" --level "$LEVEL")
  if ! echo "$WI_VERIFY" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
    harness_print_json "$WI_VERIFY"
    exit 0
  fi
fi

WS_DIR="$AGENT_WS/tasks/$WORK_ITEM_ID"
mkdir -p "$WS_DIR"
CONTEXT_FILE="$WS_DIR/context.md"
LEGACY_CONTEXT="$AGENT_WS/context.md"
BASELINE_FILE="$WS_DIR/worktree_baseline.json"

TASK_SCOPE=$(python3 - "$TASK_DIR" "$PRODUCT_ROOT" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).resolve().relative_to(Path(sys.argv[2]).resolve()))
PY
)
RUNTIME_TIER="standard"
[[ "$LEVEL" == "L3" ]] && RUNTIME_TIER="strict"
RUNTIME_SCOPE_ARGS=(--scope "$TASK_SCOPE")
PLAN_FILE="$TASK_DIR/03-实施方案.md"
if [[ -f "$PLAN_FILE" ]]; then
  while IFS= read -r SCOPE_PATH; do
    [[ -n "$SCOPE_PATH" ]] && RUNTIME_SCOPE_ARGS+=(--scope "$SCOPE_PATH")
  done < <(python3 - "$SCRIPT_DIR" "$HARNESS_ROOT" "$PRODUCT_ROOT" "$PLAN_FILE" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from business_paths import find_business_paths, load_business_roots
from task_contract_check import parse_task_rows

text = Path(sys.argv[4]).read_text(encoding="utf-8", errors="ignore")
rows, _ = parse_task_rows(text)
roots = load_business_roots(Path(sys.argv[2]), product_root=Path(sys.argv[3]))
paths = {
    path.rstrip("/")
    for row in rows
    for path in find_business_paths(row.get("write_files", ""), roots)
    if path.strip()
}
print("\n".join(sorted(paths)))
PY
)
fi
RUNTIME_START=$("$SCRIPT_DIR/harness" start "$WORK_ITEM_ID" \
  --work-item "$WORK_ITEM_ID" --tier "$RUNTIME_TIER" "${RUNTIME_SCOPE_ARGS[@]}" || true)
if ! echo "$RUNTIME_START" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
  harness_print_json "$RUNTIME_START"
  exit 0
fi

if [[ "$LEVEL" == "L3" ]]; then
  CURRENT_STAGE=$(echo "$RUNTIME_START" | python3 -c "import sys,json; d=json.load(sys.stdin); print(((d.get('result') or {}).get('cycle') or {}).get('current_stage') or '')" 2>/dev/null || echo "")
  if [[ -z "$CURRENT_STAGE" ]]; then
    IMPLEMENTATION_STAGE=$("$SCRIPT_DIR/harness" stage "$WORK_ITEM_ID" start implementation_test || true)
    if ! echo "$IMPLEMENTATION_STAGE" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin).get('decision')=='pass' else 1)" 2>/dev/null; then
      harness_print_json "$IMPLEMENTATION_STAGE"
      exit 0
    fi
  elif [[ "$CURRENT_STAGE" != "implementation_test" ]]; then
    python3 "$EMIT" block "STAGE_RESUME_CONFLICT: expected implementation_test, found $CURRENT_STAGE"
    exit 0
  fi
fi

if [[ ! -f "$BASELINE_FILE" ]]; then
  python3 "$SCRIPT_DIR/worktree_baseline.py" capture \
    --repo "$PRODUCT_ROOT" \
    --output "$BASELINE_FILE" \
    --work-item-id "$WORK_ITEM_ID" >/dev/null
fi

python3 - "$CONTEXT_FILE" "$LEGACY_CONTEXT" "$GATE_FILE" "$WORK_ITEM_ID" "$LEVEL" "$TASK_DIR" "$PRODUCT_ROOT" "$SCRIPT_DIR" "$TASKS_ROOT" "$HARNESS_ROOT" <<'PY'
import json, subprocess, sys
from pathlib import Path

ctx_path, legacy_path, gate_path, wi_id, level, task_dir, product_root, script_dir, tasks_root, harness_root = sys.argv[1:11]
gate = json.loads(Path(gate_path).read_text(encoding="utf-8"))
provider = (gate.get("work_item") or {}).get("provider", "noop")
title, note = "", ""
try:
    out = subprocess.check_output(
        ["bash", f"{script_dir}/work_item.sh", "pull", "--id", wi_id],
        text=True,
    )
    data = json.loads(out)
    if data.get("decision") == "pass":
        w = data.get("work_item") or {}
        title = w.get("title") or ""
        note = (w.get("note") or "")[:500]
except Exception:
    pass

def excerpt(path: Path, label: str, rel_path: str, limit: int = 2200) -> str:
    if not path.is_file():
        return f"### {label}\n\n- 路径: `{rel_path}`\n- 状态: 文件不存在；先运行 `harness_knowledge.sh ensure` 或确认产品 workspace 配置。\n"
    raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not raw:
        return f"### {label}\n\n- 路径: `{rel_path}`\n- 状态: 文件为空。\n"
    body = raw
    if len(body) > limit:
        body = body[:limit].rstrip() + "\n\n...（已截断；需要时读取完整文件）"
    return f"### {label}\n\n- 路径: `{rel_path}`\n\n{body}\n"

try:
    sys.path.insert(0, str(Path(script_dir)))
    from harness_knowledge import ensure
    from harness_context_index import render_index, write_index
    from workspace_paths import load_layout

    layout = load_layout(Path(harness_root).resolve(), Path(product_root).resolve())
    ensure(layout)
    index = write_index(layout, Path(ctx_path).parent / "context_index.json")
    knowledge_block = render_index(index)
except Exception as exc:
    knowledge_block = "\n".join(
        [
            "## 产品知识注入",
            "",
            f"- 状态: 读取产品知识失败：{exc}",
            "- 处理: 先运行 `harness_knowledge.sh ensure` 并确认产品侧 `harness-workspace/project.yaml`。",
        ]
    )

text = f"""# 任务上下文: Work Item {wi_id}

## BMAD Planning Gate
- 级别: {level}
- 任务目录: {task_dir}
- 产品根: {product_root}
- Work Item Provider: {provider}
- Work Item ID: {wi_id}
- 工作区: harness-workspace/runs/tasks/{wi_id}/
- 凭证: harness-workspace/planning/tasks/.../planning_gate_pass.json（兼容 phase0_pass.json，已激活）

## 来自协同系统（pull）
- 标题: {title or "（见 planning/tasks/00 与 product-specs）"}
- 备注摘要: {note or "（读本地文档）"}

{knowledge_block}

## Agent 工作指南
1. harness-engineering/AGENTS.md → docs/USAGE.md → docs/COLLABORATION.md
2. Lead: tasks-dag.md + 03-实施方案路径表
3. 写前: structure_guard.sh --path；写后: structure_guard.sh --diff + plan_sync_check.sh
4. 完成: qa_sign_off → subagent-pr-gate → harness_growth review/apply → check.sh → provider ready/review → MR；done 仅由 merge/release CI receipt 写入
"""
Path(ctx_path).write_text(text, encoding="utf-8")
Path(legacy_path).write_text(text, encoding="utf-8")
PY

bash "$SCRIPT_DIR/work_item.sh" close --id "$WORK_ITEM_ID" --status in_progress --note "Harness Execution started" >/dev/null 2>&1 || true

echo "[*] 产品根: $PRODUCT_ROOT" >&2
echo "[*] Execution 工作区: harness-workspace/runs/tasks/${WORK_ITEM_ID}/" >&2
echo "[*] 分支建议: feature/wi-${WORK_ITEM_ID}" >&2
python3 "$EMIT" pass "AGENT_READY: Harness Execution 已启动（Work Item ${WORK_ITEM_ID}）→ ${CONTEXT_FILE}"
