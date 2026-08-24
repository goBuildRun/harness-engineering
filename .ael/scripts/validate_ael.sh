#!/usr/bin/env bash
# validate_ael.sh — 读取 ael-manifest.yaml 机械校验
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
EMIT="$SCRIPT_DIR/emit_json.py"
MANIFEST="$ROOT_DIR/.ael/ael-manifest.yaml"
PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"

cd "$ROOT_DIR"

if [[ ! -f "$MANIFEST" ]]; then
  python3 "$EMIT" block "MISSING_MANIFEST: $MANIFEST"
  exit 0
fi

RESULT=$(python3 - "$MANIFEST" "$ROOT_DIR" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("BLOCK:MISSING_PYYAML")
    sys.exit(0)

manifest_path, root = Path(sys.argv[1]), Path(sys.argv[2])
m = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
fails = []

for rel in m.get("required_files") or []:
    if not (root / rel).is_file():
        fails.append(f"MISSING_FILE:{rel}")

for agent in m.get("required_agents") or []:
    p = root / f".ael/agents/{agent}.yaml"
    if not p.is_file():
        fails.append(f"MISSING_AGENT:{agent}")
    else:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("system_prompt_ref:"):
                ref = line.split(":", 1)[1].strip().strip('"')
                if not (root / ref).is_file():
                    fails.append(f"BAD_AGENT_REF:{agent}->{ref}")

agents_md = root / "AGENTS.md"
if agents_md.is_file():
    text = agents_md.read_text(encoding="utf-8")
    max_lines = int(m.get("agents_max_lines") or 150)
    if len(text.splitlines()) > max_lines:
        fails.append(f"AGENTS_TOO_LONG:{len(text.splitlines())}")
    for link in m.get("agents_must_link") or []:
        if link not in text:
            fails.append(f"AGENTS_MAP_MISSING:{link}")

print("OK" if not fails else "BLOCK:" + ";".join(fails))
PY
)

if [[ "$RESULT" == OK ]]; then
  SMOKE_FAILS=()

  json_pass() {
    echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('decision')=='pass' else 1)" 2>/dev/null
  }

  BUSINESS_SMOKE_PATH=$(python3 "$SCRIPT_DIR/business_paths.py" roots --ael-root "$ROOT_DIR" --product-root "$PRODUCT_ROOT" 2>/dev/null | python3 -c "import sys; roots=[line.strip().rstrip('/') for line in sys.stdin if line.strip()]; root=roots[0] if roots else 'ael-workspace'; print(root + '/smoke.py')" 2>/dev/null || echo "ael-workspace/smoke.py")

  FP=$(bash "$SCRIPT_DIR/feedback_planner.sh" "[ ] T1 ${BUSINESS_SMOKE_PATH} 写失败测试 [ ] 实现 [ ] 沙箱跑测试 [ ] structure_guard" 2>/dev/null || true)
  json_pass "$FP" || SMOKE_FAILS+=("SMOKE_FEEDBACK_PLANNER_BUSINESS_PATH:$FP")

	  STRUCT_GENERIC_README=$(python3 "$SCRIPT_DIR/structure_check.py" --ael-root "$ROOT_DIR" --product-root "$PRODUCT_ROOT" --profile generic --path README.md 2>/dev/null || true)
	  json_pass "$STRUCT_GENERIC_README" || SMOKE_FAILS+=("SMOKE_STRUCTURE_GENERIC_README:$STRUCT_GENERIC_README")

	  STRUCT_GENERIC_ENV=$(python3 "$SCRIPT_DIR/structure_check.py" --ael-root "$ROOT_DIR" --product-root "$PRODUCT_ROOT" --profile generic --path .env 2>/dev/null || true)
	  if json_pass "$STRUCT_GENERIC_ENV"; then
	    SMOKE_FAILS+=("SMOKE_STRUCTURE_GENERIC_ENV_NOT_BLOCKED:$STRUCT_GENERIC_ENV")
	  fi

  PLAN_SMOKE=$(python3 - "$ROOT_DIR" "$PRODUCT_ROOT" "$SCRIPT_DIR" <<'PY'
import sys
import tempfile
from pathlib import Path

root, product_root, script_dir = map(Path, sys.argv[1:4])
sys.path.insert(0, str(script_dir))

from business_paths import load_business_roots
from workspace_paths import load_layout
from plan_sync_check import extract_planned_paths

layout = load_layout(root, product_root)
business_root = (load_business_roots(root, product_root=product_root) or ("ael-workspace/",))[0].rstrip("/")
with tempfile.TemporaryDirectory() as tmp:
    task = Path(tmp)
    expected = f"{business_root}/smoke.py"
    (task / "03-实施方案.md").write_text(f"- 目标路径: `{expected}`\n", encoding="utf-8")
    paths = extract_planned_paths(layout, str(task))
    print("OK" if expected in paths else f"PLAN_SYNC_EXTRACT_MISS:{sorted(paths)}")
PY
  )
  if [[ "$PLAN_SMOKE" != "OK" ]]; then
    SMOKE_FAILS+=("SMOKE_PLAN_SYNC:$PLAN_SMOKE")
  fi

  KNOWLEDGE=$(bash "$SCRIPT_DIR/ael_knowledge.sh" paths 2>/dev/null || true)
  json_pass "$KNOWLEDGE" || SMOKE_FAILS+=("SMOKE_KNOWLEDGE_PATHS:$KNOWLEDGE")

  CAPABILITIES=$(python3 "$SCRIPT_DIR/capability_contract.py" "$ROOT_DIR" 2>/dev/null || true)
  json_pass "$CAPABILITIES" || SMOKE_FAILS+=("SMOKE_CAPABILITY_CONTRACT:$CAPABILITIES")

  PRODUCT_LIST=$(bash "$SCRIPT_DIR/ael_init.sh" list 2>/dev/null || true)
  json_pass "$PRODUCT_LIST" || SMOKE_FAILS+=("SMOKE_PRODUCT_LIST:$PRODUCT_LIST")

  GROWTH=$(python3 - "$ROOT_DIR" "$SCRIPT_DIR" <<'PY'
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root, script_dir = map(Path, sys.argv[1:3])
with tempfile.TemporaryDirectory() as tmp:
    env = os.environ.copy()
    env["AEL_PRODUCT_ROOT"] = tmp
    out = subprocess.check_output(
        ["bash", str(script_dir / "ael_growth.sh"), "status"],
        cwd=root,
        env=env,
        text=True,
    )
    print(out.strip())
PY
  )
  json_pass "$GROWTH" || SMOKE_FAILS+=("SMOKE_GROWTH_STATUS:$GROWTH")

  # QA evidence is a task-state gate, not a runtime smoke test. A freshly
  # initialized product workspace has no Planning Gate credential yet; check.sh
  # enforces qa_evidence_check.sh after BMAD Planning has passed.

  CONTRACT_SMOKE=$(python3 - "$ROOT_DIR" "$PRODUCT_ROOT" "$SCRIPT_DIR" <<'PY'
import subprocess
import sys
import tempfile
from pathlib import Path

root, _product_root, script_dir = map(Path, sys.argv[1:4])
sys.path.insert(0, str(script_dir))

from business_paths import load_business_roots

business_root = (load_business_roots(root, product_root=_product_root) or ("ael-workspace/",))[0].rstrip("/")
smoke_file = f"{business_root}/smoke.py"
smoke_dir = business_root + "/"
with tempfile.TemporaryDirectory() as tmp:
    task = Path(tmp)
    plan = task / "03-实施方案.md"
    plan.write_text(
        f"""
| ID | name | owner | depends_on | read_files | write_files | action | verify | done |
|----|------|-------|------------|------------|-------------|--------|--------|------|
| T1 | health | backend-agent | 无 | `{smoke_dir}` | `{smoke_file}` | 写失败测试并实现 | `python3 -m unittest discover -s tests` | AC-1 通过 |
""",
        encoding="utf-8",
    )
    out = subprocess.check_output(
        ["bash", str(script_dir / "task_contract_check.sh"), "--task-dir", str(task)],
        cwd=root,
        text=True,
    )
    print(out.strip())
PY
  )
  json_pass "$CONTRACT_SMOKE" || SMOKE_FAILS+=("SMOKE_TASK_CONTRACT:$CONTRACT_SMOKE")

  DAG_SMOKE=$(python3 - "$ROOT_DIR" "$PRODUCT_ROOT" "$SCRIPT_DIR" <<'PY'
import json
import subprocess
import sys
import tempfile
from pathlib import Path

root, _product_root, script_dir = map(Path, sys.argv[1:4])
with tempfile.TemporaryDirectory() as tmp:
    task = Path(tmp) / "task"
    task.mkdir()
    (task / "03-实施方案.md").write_text(
        """
| ID | name | owner | depends_on | read_files | write_files | action | verify | done |
|----|------|-------|------------|------------|-------------|--------|--------|------|
| T1 | health | backend-agent | 无 | `services/catalog_service/app/api/` | `services/catalog_service/app/api/health.py` | 写失败测试并实现 | `python3 -m unittest discover -s tests` | AC-1 通过 |
""",
        encoding="utf-8",
    )
    dag = Path(tmp) / "tasks-dag.md"
    dag.write_text("- [ ] T1: health / 负责人: backend-agent / 前置依赖: 无\n", encoding="utf-8")
    out = subprocess.check_output(
        [
            "python3",
            str(script_dir / "dag_sync_check.py"),
            "--ael-root",
            str(root),
            "--product-root",
            str(_product_root),
            "--task-dir",
            str(task),
            "--dag-file",
            str(dag),
        ],
        cwd=root,
        text=True,
    )
    print(out.strip())
PY
  )
  json_pass "$DAG_SMOKE" || SMOKE_FAILS+=("SMOKE_DAG_SYNC:$DAG_SMOKE")

  SANDBOX_SMOKE=$(bash "$SCRIPT_DIR/run_in_sandbox.sh" true 2>/dev/null || true)
  json_pass "$SANDBOX_SMOKE" || SMOKE_FAILS+=("SMOKE_SANDBOX_TRUE:$SANDBOX_SMOKE")

	  SANDBOX_BLOCK=$(bash "$SCRIPT_DIR/run_in_sandbox.sh" 'echo ok; echo bad' 2>/dev/null || true)
	  if json_pass "$SANDBOX_BLOCK"; then
	    SMOKE_FAILS+=("SMOKE_SANDBOX_CONTROL_NOT_BLOCKED:$SANDBOX_BLOCK")
	  fi

	  FEISHU_PROVIDER_TEST=$(python3 -m unittest tests.test_feishu_provider 2>&1 || true)
	  if [[ "$FEISHU_PROVIDER_TEST" != *"OK"* ]]; then
	    SMOKE_FAILS+=("SMOKE_FEISHU_PROVIDER_TEST:$FEISHU_PROVIDER_TEST")
	  fi

	  if [[ ${#SMOKE_FAILS[@]} -gt 0 ]]; then
    REASON=$(printf '%s; ' "${SMOKE_FAILS[@]}")
    python3 "$EMIT" block "AEL_INVALID: $REASON"
    exit 0
  fi

  python3 "$EMIT" pass "AEL_VALID: manifest + smoke 全项通过"
elif [[ "$RESULT" == BLOCK:MISSING_PYYAML ]]; then
  python3 "$EMIT" block "AEL_INVALID: 请 pip install pyyaml"
else
  REASON="${RESULT#BLOCK:}"
  python3 "$EMIT" block "AEL_INVALID: $REASON"
fi
