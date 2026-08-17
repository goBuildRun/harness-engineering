#!/usr/bin/env python3
"""Fail-closed execution task resolution across shared Harness workspace formats."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
    path = product / "harness-workspace" / "runs" / "active_task.json"
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
    if ((existing_task and existing_task != task_id)
            or (existing_work_item and work_item_id and existing_work_item != work_item_id)):
        return False
    active["task_id"] = task_id
    if work_item_id:
        active["work_item_id"] = work_item_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def resolve_task_id(product: Path, requested: str | None) -> tuple[str, list[str]]:
    if requested:
        return requested, []
    runs = product / "harness-workspace" / "runs"
    active_identity = ""
    direct_id = ""
    try:
        active = json.loads((runs / "active_task.json").read_text(encoding="utf-8"))
        task_id = str(active.get("task_id") or "").strip()
        work_item_id = str(active.get("work_item_id") or "").strip()
        direct_id = task_id or work_item_id
        active_identity = work_item_id or task_id
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        pass

    tasks_root = runs / "tasks"
    if direct_id:
        direct = tasks_root / direct_id / "result.json"
        if direct.is_file() and _valid_result(direct) is not None:
            return direct_id, []

    result_paths = sorted(tasks_root.glob("*/result.json")) if tasks_root.is_dir() else []
    valid_results = [(path, result) for path in result_paths if (result := _valid_result(path))]
    if active_identity:
        matching = [path.parent.name for path, result in valid_results
                    if _work_item_id(result) == active_identity]
        if len(matching) == 1:
            return matching[0], []
    candidates = sorted(path.parent.name for path, result in valid_results
                        if result.get("state") in {"active", "blocked"})
    return (candidates[0], []) if len(candidates) == 1 else ("", candidates)
