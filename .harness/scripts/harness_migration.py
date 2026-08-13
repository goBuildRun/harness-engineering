#!/usr/bin/env python3
"""Read-only legacy workspace classification for minimal task migration."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness_runtime import atomic_write_result, canonical_digest, load_result, workspace_root


COMPLETED = re.compile(
    r"当前状态\s*[:：]\s*(?:已完成|完成|done|completed)|status\s*[:：]\s*(?:done|completed)",
    re.IGNORECASE,
)


def task_is_completed(task_dir: Path) -> bool:
    card = task_dir / "00-任务卡.md"
    try:
        return bool(COMPLETED.search(card.read_text(encoding="utf-8", errors="ignore")))
    except OSError:
        return False


def audit_workspace(product: Path) -> dict[str, list[str]]:
    tasks = workspace_root(product) / "planning" / "tasks"
    runs = workspace_root(product) / "runs" / "tasks"
    report: dict[str, list[str]] = {
        "new_format": [], "legacy": [], "needs_migration": [], "missing_credentials": [],
    }
    if not tasks.is_dir():
        return report
    for task in sorted(path for path in tasks.iterdir() if path.is_dir() and not path.name.startswith("_")):
        task_id = task.name
        if (runs / task_id / "result.json").is_file():
            report["new_format"].append(task_id)
        elif task_is_completed(task):
            report["legacy"].append(task_id)
        elif (task / "planning_gate_pass.json").is_file() or (task / "phase0_pass.json").is_file():
            report["needs_migration"].append(task_id)
        else:
            report["missing_credentials"].append(task_id)
    return report


def migration_eligibility(product: Path, task_id: str) -> tuple[bool, str]:
    report = audit_workspace(product)
    if task_id in report["needs_migration"]:
        return True, "TASK_MIGRATION_ELIGIBLE"
    if task_id in report["legacy"]:
        return False, "COMPLETED_LEGACY_MIGRATION_FORBIDDEN"
    if task_id in report["missing_credentials"]:
        return False, "MIGRATION_CREDENTIAL_MISSING"
    if task_id in report["new_format"]:
        return False, "TASK_ALREADY_MIGRATED"
    return False, "MIGRATION_TASK_NOT_FOUND"


def repair_migrated_identity(path: Path, task_id: str,
                             work_item: dict[str, str] | None) -> tuple[dict, bool]:
    result = load_result(path)
    if result.get("work_item") or not work_item:
        return result, False
    result["work_item"] = work_item
    result["invariants"]["task_identity"] = "pass"
    result["binding_digest"] = canonical_digest(
        {"task_id": task_id, "work_item": work_item,
         "tier_floor": "standard", "source": "migration"})
    atomic_write_result(path, result)
    return result, True
