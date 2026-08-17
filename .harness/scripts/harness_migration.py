#!/usr/bin/env python3
"""Read-only legacy workspace classification for minimal task migration."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harness_runtime import atomic_write_result, canonical_digest, load_result, workspace_root
from harness_task_resolution import valid_task_id


TASK_ID = re.compile(r"(?:Harness Task ID|任务编号)\s*[:：]\s*([^\s]+)", re.IGNORECASE)
STATUS = re.compile(r"(?:当前状态|status)\s*[:：]\s*([^\r\n]+)", re.IGNORECASE)
COMPLETED = re.compile(r"^(?:已完成|完成|done|complete|completed)\b", re.IGNORECASE)
ACTIVE = re.compile(
    r"^(?:进行中|实施中|in[_ -]?progress|open\b|testing\b|verification\b|"
    r"implementation-complete\b|semantic-continuity implementation-complete\b|"
    r"ready-for-(?:dev|qa)\b|awaiting-real-device-acceptance\b)",
    re.IGNORECASE,
)


def task_card(task_dir: Path) -> str:
    try:
        return (task_dir / "00-任务卡.md").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def stable_task_id(task_dir: Path) -> str:
    match = TASK_ID.search(task_card(task_dir))
    return match.group(1).strip("`'") if match else task_dir.name


def task_status(task_dir: Path) -> str:
    match = STATUS.search(task_card(task_dir))
    return match.group(1).strip() if match else ""


def task_is_completed(task_dir: Path) -> bool:
    return bool(COMPLETED.search(task_status(task_dir)))


def task_is_active(task_dir: Path) -> bool:
    return bool(ACTIVE.search(task_status(task_dir)))


def has_valid_credential(task_dir: Path) -> bool:
    for name in ("planning_gate_pass.json", "phase0_pass.json"):
        try:
            credential = json.loads((task_dir / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(credential, dict) and credential.get("decision") == "pass":
            return True
    return False


def audit_workspace(product: Path) -> dict[str, list[str]]:
    tasks = workspace_root(product) / "planning" / "tasks"
    runs = workspace_root(product) / "runs" / "tasks"
    report: dict[str, list[str]] = {
        "new_format": [], "legacy": [], "needs_migration": [], "missing_credentials": [],
        "invalid_task_ids": [],
    }
    if not tasks.is_dir():
        return report
    for task in sorted(path for path in tasks.iterdir() if path.is_dir() and not path.name.startswith("_")):
        task_id = stable_task_id(task)
        if not valid_task_id(task_id):
            report["invalid_task_ids"].append(task_id)
            continue
        if (runs / task_id / "result.json").is_file():
            report["new_format"].append(task_id)
        elif task_is_completed(task):
            report["legacy"].append(task_id)
        elif task_is_active(task) and has_valid_credential(task):
            report["needs_migration"].append(task_id)
        elif task_is_active(task):
            report["missing_credentials"].append(task_id)
        else:
            # Backlog, paused and ambiguous historical cards stay readable in place.
            # They become migration candidates only after an explicit active status.
            report["legacy"].append(task_id)
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
