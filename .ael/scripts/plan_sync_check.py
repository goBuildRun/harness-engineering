#!/usr/bin/env python3
"""plan_sync_check — 03-实施方案路径表 vs git diff 子集校验."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from business_paths import find_business_paths, is_business_path, load_business_roots
from ael_output import dump_json
from ael_runtime import canonical_digest
from ael_scope import paths_within_scope
from ael_task_resolution import valid_task_id
from worktree_baseline import capture_baseline, changed_since_baseline
from workspace_paths import active_planning_gate_path, load_layout


def emit(decision: str, reason: str) -> None:
    dump_json({"decision": decision, "reason": reason})


def git_changed(repo: Path) -> list[str]:
    files: list[str] = []
    for args in (["git", "diff", "--name-only"], ["git", "diff", "--cached", "--name-only"]):
        try:
            out = subprocess.check_output(args, cwd=repo, stderr=subprocess.DEVNULL, text=True)
            files.extend(line.strip() for line in out.splitlines() if line.strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return list(dict.fromkeys(files))


def active_task_baseline(layout) -> tuple[str, Path | None]:
    ci_task_id = os.environ.get("AEL_CI_TASK_ID", "").strip()
    if ci_task_id:
        if not valid_task_id(ci_task_id):
            raise ValueError("TASK_ID_INVALID")
        return (
            ci_task_id,
            layout.agent_workspace / "tasks" / ci_task_id / "worktree_baseline.json",
        )
    task_id = os.environ.get("AEL_TASK_ID", "").strip()
    if task_id:
        if not valid_task_id(task_id):
            raise ValueError("TASK_ID_INVALID")
        return task_id, layout.agent_workspace / "tasks" / task_id / "worktree_baseline.json"
    active_path = layout.agent_workspace / "active_task.json"
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "", None
    work_item_id = str(active.get("work_item_id") or active.get("task_id") or "").strip()
    if not work_item_id:
        return "", None
    if not valid_task_id(work_item_id):
        raise ValueError("TASK_ID_INVALID")
    return work_item_id, layout.agent_workspace / "tasks" / work_item_id / "worktree_baseline.json"


def active_task_planning_dir(layout) -> Path | None:
    if os.environ.get("AEL_CI_TASK_ID", "").strip() or os.environ.get("AEL_TASK_ID", "").strip():
        gate_path = active_planning_gate_path(layout)
        try:
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            raw = str(gate.get("task_dir") or "").strip()
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = layout.product_root / candidate
            candidate = candidate.resolve()
            candidate.relative_to(layout.tasks.resolve())
        except (AttributeError, json.JSONDecodeError, OSError, ValueError):
            return None
        return candidate if candidate.is_dir() else None
    work_item_id, _ = active_task_baseline(layout)
    if not work_item_id or not layout.tasks.is_dir():
        return None
    exact = layout.tasks / work_item_id
    if exact.is_dir():
        return exact
    matches = [path for path in layout.tasks.iterdir() if path.is_dir() and work_item_id in path.name]
    return matches[0] if len(matches) == 1 else None


def task_changed(layout) -> list[str]:
    if os.environ.get("AEL_CI_TASK_ID", "").strip():
        try:
            credential = json.loads(
                active_planning_gate_path(layout).read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError("CI_CHANGED_FILES_BINDING_INVALID") from exc
        changed = credential.get("changed_files")
        expected_digest = os.environ.get("AEL_CI_CHANGED_FILES_DIGEST", "").strip()
        if (
            credential.get("schema") != "harness-ci-planning-environment-v1"
            or not isinstance(changed, list)
            or not all(isinstance(path, str) for path in changed)
            or len(changed) != len(set(changed))
            or not expected_digest
            or credential.get("changed_files_digest") != canonical_digest(changed)
            or credential.get("changed_files_digest") != expected_digest
        ):
            raise ValueError("CI_CHANGED_FILES_BINDING_INVALID")
        return changed
    _, baseline = active_task_baseline(layout)
    if baseline is not None and baseline.is_file():
        return changed_since_baseline(layout.product_root, baseline)
    return git_changed(layout.product_root)


def extract_planned_paths(layout, task_dir: str | None) -> set[str]:
    paths: set[str] = set()
    business_roots = load_business_roots(layout.ael_root, product_root=layout.product_root)
    candidates: list[Path] = []
    if task_dir:
        p = Path(task_dir)
        if not p.is_absolute():
            p = layout.tasks / task_dir
        if not p.is_dir():
            p = Path(task_dir)
        candidates.append(p / "03-实施方案.md")
    if not candidates and layout.tasks.is_dir():
        for d in sorted(layout.tasks.iterdir(), reverse=True):
            if d.is_dir() and not d.name.startswith("_"):
                candidates.append(d / "03-实施方案.md")
                break
    for c in candidates:
        if not c.is_file():
            continue
        text = c.read_text(encoding="utf-8", errors="ignore")
        paths.update(find_business_paths(text, business_roots))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ael-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--product-id", default="")
    parser.add_argument("--task-dir", default="")
    parser.add_argument(
        "--recover-baseline",
        default="",
        metavar="REASON",
        help="create an audited baseline for an active task started before baseline support",
    )
    args = parser.parse_args()

    layout = load_layout(
        Path(args.ael_root).resolve(),
        Path(args.product_root).resolve() if (args.product_root) else None,
        args.product_id,
    )

    task_dir = args.task_dir
    try:
        active_dir = active_task_planning_dir(layout)
    except ValueError as exc:
        emit("block", f"PLAN_SYNC_ACTIVE_TASK_INVALID: {exc}")
        return 0
    if not task_dir and active_dir is not None and (active_dir / "03-实施方案.md").is_file():
        task_dir = str(active_dir)
    gate = active_planning_gate_path(layout)
    if not task_dir and gate.is_file():
        try:
            task_dir = json.loads(gate.read_text(encoding="utf-8")).get("task_dir") or ""
        except (json.JSONDecodeError, OSError):
            pass

    if args.recover_baseline:
        work_item_id, baseline = active_task_baseline(layout)
        if not work_item_id or baseline is None:
            emit("block", "PLAN_SYNC_BASELINE_RECOVERY_NO_ACTIVE_TASK")
            return 0
        planned = extract_planned_paths(layout, task_dir)
        if not planned:
            emit("block", "PLAN_SYNC_BASELINE_RECOVERY_NO_PLAN")
            return 0
        try:
            created = capture_baseline(
                layout.product_root,
                baseline,
                work_item_id=work_item_id,
                mode="recovery",
                reason=args.recover_baseline,
                exclude_paths=planned,
            )
        except ValueError as exc:
            emit("block", f"PLAN_SYNC_BASELINE_RECOVERY_INVALID: {exc}")
            return 0
        if not created:
            emit("block", f"PLAN_SYNC_BASELINE_ALREADY_EXISTS: {baseline}")
            return 0
        emit(
            "pass",
            f"PLAN_SYNC_BASELINE_RECOVERED: {baseline}（计划内路径 {len(planned)} 个保持待审）",
        )
        return 0

    try:
        changed = task_changed(layout)
    except ValueError as exc:
        emit("block", f"PLAN_SYNC_BASELINE_INVALID: {exc}")
        return 0
    if not changed:
        emit("pass", "PLAN_SYNC_SKIP: 无 git 变更，跳过路径对照（MR 前须有变更）")
        return 0

    business_roots = load_business_roots(layout.ael_root, product_root=layout.product_root)
    business_changed = [f for f in changed if is_business_path(f, business_roots)]
    if not business_changed:
        emit("pass", "PLAN_SYNC_OK: 无业务路径变更")
        return 0

    planned = extract_planned_paths(layout, task_dir)
    if not planned:
        emit("block", f"PLAN_SYNC_NO_PLAN: 有业务变更 {business_changed} 但 03-实施方案 未登记路径")
        return 0

    unplanned = [f for f in business_changed if not paths_within_scope([f], planned)]
    if unplanned:
        emit("block", f"PLAN_SYNC_VIOLATION: 未登记路径 {unplanned}; 已登记 {sorted(planned)}")
        return 0

    emit("pass", f"PLAN_SYNC_OK: {len(business_changed)} 个变更路径均在 03-实施方案 内")
    return 0


if __name__ == "__main__":
    main()
