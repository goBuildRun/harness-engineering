#!/usr/bin/env python3
"""Execution-tier classification and task-kind risk floors."""
from __future__ import annotations

from pathlib import Path


TIERS = {"lite": 0, "standard": 1, "strict": 2}
TASK_KIND_FLOORS = {
    "implementation": "lite",
    "debt-maintenance": "standard",
    "scope-change": "standard",
    "hotfix": "strict",
    "harness-maintenance": "lite",
}
HIGH_RISK_PARTS = {
    "auth", "security", "permission", "payments", "production", "deploy",
    "migration", "migrations", "terraform", "infra", "secrets",
}
DOC_PREFIXES = ("docs/", "README", "AGENTS.md", "ARCHITECTURE.md")


def classify_tier(paths: list[str], *, floor: str = "standard", kind: str = "implementation") -> str:
    floor = floor if floor in TIERS else "standard"
    maintenance = kind == "harness-maintenance" and bool(paths) and all(
        path in {"harness-workspace/project.yaml", "harness-workspace/knowledge/HARNESS_TAKEOVER.md"}
        or path.startswith(".githooks/") for path in paths
    )
    inferred = "lite" if maintenance else "standard"
    if paths and all(path.startswith(DOC_PREFIXES) or Path(path).suffix in {".md", ".txt"} for path in paths):
        inferred = "lite"
    lowered = [part.lower() for path in paths for part in Path(path).parts]
    if any(part in HIGH_RISK_PARTS for part in lowered) or any(path.endswith(".sql") for path in paths):
        inferred = "strict"
    return max((floor, inferred), key=TIERS.__getitem__)


def task_kind_tier(kind: str, requested: str) -> str:
    floor = TASK_KIND_FLOORS.get(kind, "standard")
    requested = requested if requested in TIERS else "standard"
    return max((floor, requested), key=TIERS.__getitem__)
