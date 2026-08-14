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

if [[ "$LEVEL" == "L2" || "$LEVEL" == "L3" ]]; then
  BOUND_ID=$(python3 -c "import json; d=json.load(open('$GATE_FILE')); w=d.get('work_item') or {}; print(w.get('id') or '')")
  if [[ -z "$BOUND_ID" ]]; then
    python3 "$EMIT" block "NO_WORK_ITEM_IN_PLANNING_GATE: 重新 planning_gate"
    exit 0
  fi
  if [[ "$WORK_ITEM_ID" != "$BOUND_ID" ]]; then
    python3 "$EMIT" block "WORK_ITEM_MISMATCH: 参数 ${WORK_ITEM_ID} 与 planning gate ${BOUND_ID} 不一致"
    exit 0
  fi
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
import os, sys
print(os.path.relpath(sys.argv[1], sys.argv[2]))
PY
)
RUNTIME_START=$("$SCRIPT_DIR/harness" start "$WORK_ITEM_ID" \
  --work-item "$WORK_ITEM_ID" --tier standard --scope "$TASK_SCOPE")
if ! echo "$RUNTIME_START" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null; then
  harness_print_json "$RUNTIME_START"
  exit 0
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
    from workspace_paths import load_layout

    layout = load_layout(Path(harness_root).resolve(), Path(product_root).resolve())
    ensure(layout)
    knowledge_block = "\n".join(
        [
            "## 产品知识注入",
            "",
            "> 以下内容来自产品侧 `harness-workspace/knowledge/`。若与本次任务 `product-spec` / `03-实施方案` 冲突，以本次任务为准，并把长期差异写回成长报告。",
            "",
            excerpt(layout.context_file, "CONTEXT.md", layout.rel(layout.context_file)),
            excerpt(layout.lessons_file, "LESSONS.md", layout.rel(layout.lessons_file), 1600),
            excerpt(layout.reference_systems_file, "REFERENCE_SYSTEMS.md", layout.rel(layout.reference_systems_file), 1600),
        ]
    ).rstrip()
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
