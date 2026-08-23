#!/usr/bin/env python3
"""task_workspace.py — 多任务协作：按 Work Item ID 隔离工作区、自动恢复 planning gate。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness_output import dump_json
from harness_task_resolution import valid_task_id
from workspace_paths import Phase0Layout, load_layout


def emit(decision: str, reason: str, **extra) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def work_item_id_from_gate(data: dict | None) -> str:
    if not data:
        return ""
    wi = data.get("work_item") or {}
    return str(wi.get("id") or "") if isinstance(wi, dict) else ""


def find_gate_in_tasks(layout: Phase0Layout, work_item_id: str = "") -> Path | None:
    tasks_root = layout.tasks
    if not tasks_root.is_dir():
        return None
    for d in sorted(tasks_root.iterdir(), reverse=True):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        p = d / "planning_gate_pass.json"
        if not p.is_file():
            p = d / "phase0_pass.json"
        if not p.is_file():
            continue
        data = load_json(p)
        wid = work_item_id_from_gate(data)
        if work_item_id and wid != work_item_id:
            continue
        return p
    return None


def find_planning_child(layout: Phase0Layout, work_item_id: str) -> dict | None:
    """Find a batch child receipt without consulting a shared active pointer."""
    root = layout.agent_workspace / "planning"
    if not root.is_dir():
        return None
    candidates: list[tuple[str, int, Path, dict]] = []
    for path in root.glob("*/children/*.json"):
        data = load_json(path)
        if data and str(data.get("work_item_id") or "") == work_item_id:
            bundle = load_json(path.parent.parent / "planning-bundle.json") or {}
            stamp = str(bundle.get("finished_at") or data.get("created_at") or "")
            try:
                mtime = path.stat().st_mtime_ns
            except OSError:
                mtime = 0
            candidates.append((stamp, mtime, path, data))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1], str(item[2])))[3]


def task_workspace_dir(layout: Phase0Layout, work_item_id: str) -> Path:
    if not valid_task_id(work_item_id):
        raise ValueError("TASK_ID_INVALID")
    return layout.agent_workspace / "tasks" / work_item_id


def active_task_file(layout: Phase0Layout) -> Path:
    return layout.agent_workspace / "active_task.json"


def active_planning_gate(layout: Phase0Layout) -> Path:
    return layout.agent_workspace / "planning_gate_pass.json"


def legacy_phase0(layout: Phase0Layout) -> Path:
    return layout.agent_workspace / "phase0_pass.json"


def legacy_context(layout: Phase0Layout) -> Path:
    return layout.agent_workspace / "context.md"


def activate_task(layout: Phase0Layout, work_item_id: str, *, task_scoped: bool = False) -> dict:
    if work_item_id and not valid_task_id(work_item_id):
        return {"ok": False, "reason": "TASK_ID_INVALID"}
    src = find_gate_in_tasks(layout, work_item_id)
    planning_child = None
    if not src and work_item_id:
        planning_child = find_planning_child(layout, work_item_id)
        if planning_child:
            gate = planning_child.get("planning_gate")
            if isinstance(gate, dict) and gate.get("decision") == "pass":
                src = None
            else:
                planning_child = None
    if not src and not planning_child:
        rel = layout.rel(layout.tasks)
        return {"ok": False, "reason": f"PLANNING_GATE_NOT_FOUND: {rel}/ 中无 work_item_id={work_item_id} 的 planning_gate_pass.json（兼容 phase0_pass.json）"}

    data = load_json(src) if src else dict(planning_child.get("planning_gate") or {})
    if not data or data.get("decision") != "pass":
        return {"ok": False, "reason": "PLANNING_GATE_INVALID: 任务目录内 planning gate 凭证无效"}

    wid = work_item_id_from_gate(data) or work_item_id
    if not valid_task_id(wid):
        return {"ok": False, "reason": "TASK_ID_INVALID"}
    ws = task_workspace_dir(layout, wid)
    ws.mkdir(parents=True, exist_ok=True)

    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    (ws / "planning_gate_pass.json").write_text(text, encoding="utf-8")
    (ws / "phase0_pass.json").write_text(text, encoding="utf-8")
    if not task_scoped:
        active_gate = active_planning_gate(layout)
        active_gate.parent.mkdir(parents=True, exist_ok=True)
        active_gate.write_text(text, encoding="utf-8")
        legacy = legacy_phase0(layout)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(text, encoding="utf-8")

    active = {
        "work_item_id": wid,
        "task_dir": data.get("task_dir"),
        "workspace_dir": layout.rel(ws),
        "planning_gate_source": layout.rel(src) if src else "batch-receipt",
        "planning_root": layout.rel(layout.planning_root),
        "phase0_source": layout.rel(src) if src else "batch-receipt",
        "phase0_root": layout.rel(layout.phase0_root),
        "activated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if not task_scoped:
        active_task_file(layout).write_text(json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "work_item_id": wid, "workspace": str(ws), "task_dir": data.get("task_dir")}


def infer_task_dir_from_git_diff(layout: Phase0Layout) -> str | None:
    files: list[str] = []
    for args in (
        ["git", "diff", "--name-only", "origin/HEAD...HEAD"],
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
    ):
        try:
            out = subprocess.check_output(
                args, cwd=layout.product_root, stderr=subprocess.DEVNULL, text=True
            )
            files.extend(line.strip() for line in out.splitlines() if line.strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    tasks_prefix = layout.tasks_git_prefix()
    task_dirs: set[str] = set()
    for f in files:
        if f.startswith(tasks_prefix):
            task_dirs.add(f[len(tasks_prefix) :].split("/")[0])
        elif f.startswith("tasks/") and layout.rel(layout.tasks).endswith("/tasks"):
            # legacy harness-relative paths in git history
            task_dirs.add(f.split("/")[1])

    if len(task_dirs) == 1:
        p = layout.tasks / next(iter(task_dirs))
        return str(p.resolve()) if p.is_dir() else None
    return None


def qa_evidence_path(layout: Phase0Layout, dag_task_id: str) -> Path:
    if not valid_task_id(dag_task_id):
        raise ValueError("TASK_ID_INVALID")
    active_path = active_task_file(layout)
    if active_path.is_file():
        active = load_json(active_path)
        if active is None:
            raise ValueError("ACTIVE_TASK_INVALID")
        wid = str(active.get("work_item_id") or active.get("task_id") or "").strip()
        if not wid:
            raise ValueError("ACTIVE_TASK_INVALID")
        return task_workspace_dir(layout, wid) / f"qa_approved_{dag_task_id}.json"
    return layout.agent_workspace / f"qa_approved_{dag_task_id}.json"


def cmd_activate(work_item_id: str, layout: Phase0Layout) -> int:
    result = activate_task(layout, work_item_id, task_scoped=False)
    if result["ok"]:
        emit("pass", f"TASK_ACTIVATED: {result['work_item_id']}", active=result)
    else:
        emit("block", result["reason"])
    return 0


def cmd_show_active(layout: Phase0Layout) -> int:
    data = load_json(active_task_file(layout))
    if not data:
        emit("block", "NO_ACTIVE_TASK")
        return 0
    emit("pass", "ACTIVE_TASK", active=data)
    return 0


def cmd_infer_mr(layout: Phase0Layout) -> int:
    env_dir = os.environ.get("HARNESS_TASK_DIR", "").strip()
    if env_dir:
        p = Path(env_dir)
        if not p.is_absolute():
            p = (layout.tasks / env_dir).resolve() if not p.exists() else (layout.product_root / env_dir).resolve()
        if p.is_dir():
            emit("pass", "MR_TASK_DIR", task_dir=str(p.resolve()), source="HARNESS_TASK_DIR")
            return 0
        emit("block", f"HARNESS_TASK_DIR_INVALID: {env_dir}")
        return 0
    inferred = infer_task_dir_from_git_diff(layout)
    if inferred:
        emit("pass", "MR_TASK_DIR", task_dir=inferred, source="git_diff")
        return 0
    rel = layout.rel(layout.tasks)
    emit("block", f"MR_TASK_DIR_UNKNOWN: 设置 HARNESS_TASK_DIR 或 MR 仅变更一个 {rel}/<id>/ 目录")
    return 0


def cmd_qa_path(dag_task_id: str, layout: Phase0Layout) -> int:
    try:
        p = qa_evidence_path(layout, dag_task_id)
    except ValueError as exc:
        emit("block", str(exc))
        return 0
    emit("pass" if p.is_file() else "block", str(p), exists=p.is_file())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show-active")
    sub.add_parser("infer-mr")
    p_act = sub.add_parser("activate")
    p_act.add_argument("work_item_id")
    p_act.add_argument("--task-scoped", action="store_true", help="仅写 runs/tasks/<id>，不覆盖全局 active 状态")
    p_qa = sub.add_parser("qa-path")
    p_qa.add_argument("dag_task_id")

    args = parser.parse_args()
    layout = load_layout(
        Path(args.harness_root).resolve(),
        Path(args.product_root).resolve() if args.product_root else None,
    )

    if args.cmd == "activate":
        result = activate_task(layout, args.work_item_id, task_scoped=args.task_scoped)
        if result["ok"]:
            emit("pass", f"TASK_ACTIVATED: {result['work_item_id']}", active=result,
                 task_scoped=args.task_scoped)
        else:
            emit("block", result["reason"])
        return 0
    if args.cmd == "show-active":
        return cmd_show_active(layout)
    if args.cmd == "infer-mr":
        return cmd_infer_mr(layout)
    if args.cmd == "qa-path":
        return cmd_qa_path(args.dag_task_id, layout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
