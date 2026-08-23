#!/usr/bin/env python3
"""Fail-closed execution task resolution across shared Harness workspace formats."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")


def valid_task_id(value: str) -> bool:
    return bool(TASK_ID_RE.fullmatch(str(value or "")))


def _load_result(path: Path) -> dict[str, Any] | None:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return result if isinstance(result, dict) else None


def _valid_result(path: Path) -> dict[str, Any] | None:
    result = _load_result(path)
    if result is None or str(result.get("task_id") or "") != path.parent.name:
        return None
    return result


def _work_item_id(result: dict[str, Any]) -> str:
    work_item = result.get("work_item") or {}
    if not isinstance(work_item, dict):
        return ""
    return str(work_item.get("id") or "").strip()


def bind_active_task(product: Path, task_id: str, work_item_id: str) -> bool:
    if not valid_task_id(task_id) or (work_item_id and not valid_task_id(work_item_id)):
        return False
    runs = product / "harness-workspace" / "runs"
    task_scoped = os.environ.get("HARNESS_TASK_SCOPED", "0") == "1"
    path = (runs / "tasks" / task_id / "active_task.json") if task_scoped else runs / "active_task.json"
    try:
        active = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        active = {}
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(active, dict):
        return False
    existing_task = str(active.get("task_id") or "").strip()
    existing_work_item = str(active.get("work_item_id") or "").strip()
    if (not task_scoped and ((existing_task and existing_task != task_id)
            or (existing_work_item and existing_work_item != work_item_id))):
        return False
    active["task_id"] = task_id
    if work_item_id:
        active["work_item_id"] = work_item_id
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=".active-task-", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(active, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        return False
    return True


def resolve_task_id(product: Path, requested: str | None) -> tuple[str, list[str]]:
    ci_task_id = os.environ.get("HARNESS_CI_TASK_ID", "").strip()
    if ci_task_id:
        if not valid_task_id(ci_task_id):
            return "", ["TASK_ID_INVALID"]
        if requested and requested != ci_task_id:
            return "", ["CI_TASK_ID_MISMATCH"]
        return ci_task_id, []
    if requested:
        return (requested, []) if valid_task_id(requested) else ("", ["TASK_ID_INVALID"])
    runs = product / "harness-workspace" / "runs"
    active_identity = ""
    direct_id = ""
    active_task_id = ""
    active_work_item_id = ""
    try:
        active = json.loads((runs / "active_task.json").read_text(encoding="utf-8"))
        active_task_id = str(active.get("task_id") or "").strip()
        active_work_item_id = str(active.get("work_item_id") or "").strip()
        direct_id = active_task_id or active_work_item_id
        active_identity = active_work_item_id or active_task_id
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        pass
    if ((active_task_id and not valid_task_id(active_task_id))
            or (active_work_item_id and not valid_task_id(active_work_item_id))):
        return "", ["TASK_ID_INVALID"]

    tasks_root = runs / "tasks"
    if direct_id:
        direct = tasks_root / direct_id / "result.json"
        direct_result = _valid_result(direct) if direct.is_file() else None
        if (direct_result is not None and active_task_id
                and (not active_work_item_id
                     or _work_item_id(direct_result) == active_work_item_id)):
            return direct_id, []
        if active_task_id:
            return "", []
        if (direct_result is not None
                and _work_item_id(direct_result) in {"", active_work_item_id}):
            return direct_id, []
        if direct_result is not None:
            return "", []

    result_paths = sorted(tasks_root.glob("*/result.json")) if tasks_root.is_dir() else []
    valid_results = [(path, result) for path in result_paths if (result := _valid_result(path))]
    if active_identity:
        matching = [path.parent.name for path, result in valid_results
                    if _work_item_id(result) == active_identity]
        if len(matching) == 1:
            return matching[0], []
        return "", matching
    candidates = sorted(path.parent.name for path, result in valid_results
                        if result.get("state") in {"active", "blocked"})
    return (candidates[0], []) if len(candidates) == 1 else ("", candidates)
