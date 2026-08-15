#!/usr/bin/env python3
"""Check tasks-dag.md against 03-实施方案 task ids."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from harness_output import dump_json
from task_contract_check import parse_task_rows
from workspace_paths import active_planning_gate_path, load_layout, resolve_task_dir


def emit(decision: str, reason: str, **extra) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def load_task_dir(layout, task_dir: str) -> Path | None:
    if task_dir:
        return resolve_task_dir(layout, task_dir, cwd=Path.cwd())
    gate = active_planning_gate_path(layout)
    if gate.is_file():
        try:
            value = json.loads(gate.read_text(encoding="utf-8")).get("task_dir") or ""
            return Path(value).resolve() if value else None
        except (json.JSONDecodeError, OSError):
            return None
    return None


def resolve_dag_file(layout, task_dir: Path | None, dag_file: str) -> Path:
    if dag_file:
        p = Path(dag_file)
        if p.is_absolute():
            return p.resolve()
        if task_dir is not None:
            task_relative = (task_dir / p).resolve()
            if task_relative.is_file() or len(p.parts) == 1:
                return task_relative
        return p.resolve()
    if task_dir is not None:
        return task_dir / "tasks-dag.md"
    return layout.tasks / "tasks-dag.md"


def parse_dag(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    owners: dict[str, str] = {}
    deps: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = re.search(r"- \[[ xX]\]\s*(T[A-Za-z0-9._-]+)\s*[:：](.*)", line)
        if not match:
            continue
        tid, rest = match.group(1), match.group(2)
        owner = ""
        owner_match = re.search(r"负责人\s*[:：]\s*([^/，,]+)", rest)
        if owner_match:
            owner = owner_match.group(1).strip()
        dep_values: list[str] = []
        dep_match = re.search(r"前置依赖\s*[:：]\s*([^/，,]+(?:[,，]\s*[^/，,]+)*)", rest)
        if dep_match:
            raw = dep_match.group(1).strip()
            if raw not in {"无", "none", "None", "-"}:
                dep_values = [d.strip() for d in re.split(r"[,，]", raw) if d.strip()]
        owners[tid] = owner
        deps[tid] = dep_values
    return owners, deps


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--task-dir", default="")
    parser.add_argument("--dag-file", default="")
    parser.add_argument("--allow-skip", action="store_true", help="显式允许无 DAG/无 task_dir 的诊断场景跳过")
    args = parser.parse_args()

    layout = load_layout(
        Path(args.harness_root).resolve(),
        Path(args.product_root).resolve() if args.product_root else None,
    )
    task_dir = load_task_dir(layout, args.task_dir)
    if not task_dir:
        if args.allow_skip:
            emit("pass", "DAG_SYNC_SKIP: 未激活 task_dir（显式 allow-skip）")
        else:
            emit("block", "DAG_SYNC_NO_TASK_DIR: 未激活 task_dir；请先 agent_start 或传 --task-dir")
        return 0

    dag_file = resolve_dag_file(layout, task_dir, args.dag_file)
    if not dag_file.is_file():
        if args.allow_skip:
            emit("pass", "DAG_SYNC_SKIP: 未找到 tasks-dag.md（显式 allow-skip）")
        else:
            emit(
                "block",
                "DAG_SYNC_MISSING: 未找到任务目录内 tasks-dag.md；L2/L3 必须把 DAG 放在产品侧任务目录，诊断场景请显式传 --allow-skip",
                expected=str(dag_file),
            )
        return 0
    plan = task_dir / "03-实施方案.md"
    if not plan.is_file():
        emit("block", f"DAG_SYNC_PLAN_MISSING: {plan}")
        return 0

    rows, row_issues = parse_task_rows(plan.read_text(encoding="utf-8", errors="ignore"))
    if row_issues or not rows:
        emit("block", "DAG_SYNC_PLAN_INVALID: 03-实施方案缺少有效任务契约")
        return 0

    plan_ids = {row["id"].strip("` ") for row in rows}
    dag_owners, dag_deps = parse_dag(dag_file.read_text(encoding="utf-8", errors="ignore"))
    dag_ids = set(dag_owners)
    implementation_dag_ids = {
        tid for tid, owner in dag_owners.items() if owner not in {"qa-evaluator", "gc-sweeper"}
    }

    issues: list[str] = []
    missing = sorted(plan_ids - dag_ids)
    if missing:
        issues.append(f"DAG_MISSING_PLAN_TASKS:{missing}")
    extra_impl = sorted(implementation_dag_ids - plan_ids)
    if extra_impl:
        issues.append(f"DAG_EXTRA_IMPL_TASKS:{extra_impl}")
    for tid, deps in dag_deps.items():
        unknown = [dep for dep in deps if dep not in dag_ids]
        if unknown:
            issues.append(f"DAG_UNKNOWN_DEP:{tid}->{unknown}")

    if issues:
        emit("block", "DAG_SYNC_INVALID: " + "; ".join(issues))
        return 0

    emit("pass", f"DAG_SYNC_OK: {len(plan_ids)} 个实施任务与 DAG 对齐")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
