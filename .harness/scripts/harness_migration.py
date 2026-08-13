#!/usr/bin/env python3
"""Read-only legacy workspace classification for minimal task migration."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harness_runtime import atomic_write_result, canonical_digest, load_result, workspace_root


STATUS = re.compile(r"(?:当前状态|状态|status)\s*[:：]\s*([^\n\r]+)", re.IGNORECASE)
COMPLETED = re.compile(r"^(?:已完成|完成|done|complete(?:d)?|closed)(?:\b|[（(；;，,。/ ]|$)", re.IGNORECASE)
ACTIVE = re.compile(
    r"(?:进行中|执行中|实施中|待验收|待部署|待完成|待同步|待回写|待复测|重开|"
    r"active|in[-_ ]progress|reopen(?:ed)?|testing|verification|pending|awaiting)",
    re.IGNORECASE,
)


def task_status(task_dir: Path) -> str:
    card = task_dir / "00-任务卡.md"
    try:
        body = card.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "unknown"
    match = STATUS.search(body)
    if not match:
        return "unknown"
    value = match.group(1).strip().strip("`*_ ").lower()
    if ACTIVE.search(value):
        return "active"
    if COMPLETED.search(value):
        return "completed"
    return "unknown"


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
        "new_format": [], "legacy": [], "needs_migration": [],
        "missing_credentials": [], "unknown_status": [],
    }
    if not tasks.is_dir():
        return report
    for task in sorted(path for path in tasks.iterdir() if path.is_dir() and not path.name.startswith("_")):
        task_id = task.name
        status = task_status(task)
        if (runs / task_id / "result.json").is_file():
            report["new_format"].append(task_id)
        elif status == "completed":
            report["legacy"].append(task_id)
        elif status == "active" and has_valid_credential(task):
            report["needs_migration"].append(task_id)
        elif status == "unknown":
            report["unknown_status"].append(task_id)
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
    if task_id in report["unknown_status"]:
        return False, "MIGRATION_STATUS_UNKNOWN"
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
