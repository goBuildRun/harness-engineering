#!/usr/bin/env python3
"""Task scope matching shared by local and CI AEL decisions."""
from __future__ import annotations

from pathlib import Path


def execution_paths(product: Path, task_id: str, changed: list[str]) -> list[str]:
    workspace = product / "ael-workspace"
    generated = workspace / "planning" / "tasks" / task_id / "task.json"
    try:
        generated_rel = str(generated.relative_to(product))
    except ValueError:
        generated_rel = ""
    runs = str((workspace / "runs").relative_to(product)).rstrip("/") + "/"
    return [path for path in changed if path != generated_rel and not path.startswith(runs)]


def paths_within_scope(paths: list[str], scopes: list[str] | set[str]) -> bool:
    normalized = {scope.rstrip("/") or "." for scope in scopes}
    if "." in normalized:
        return True
    return all(
        any(path == scope or path.startswith(scope + "/") for scope in normalized)
        for path in paths
    )
