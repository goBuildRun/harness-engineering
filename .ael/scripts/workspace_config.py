#!/usr/bin/env python3
"""Product workspace configuration normalization and legacy overlay support."""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

DEFAULT_PHASE0 = {
    "root": "ael-workspace/planning",
    "product_specs": "product-specs",
    "exec_plans_active": "exec-plans/active",
    "exec_plans_completed": "exec-plans/completed",
    "tasks": "tasks",
    "agent_workspace": "../runs",
}
DEFAULT_BMAD = {"install_root": "."}
DEFAULT_HARNESS = {
    "name": "BuildRun Agent Engineering Lifecycle",
    "profile": "generic",
    "product_name": "Product",
    "product_id": "product",
    "task_contract": "harness-task-v1",
    "knowledge_root": "../knowledge",
    "evidence_root": "../evidence",
    "context_file": "CONTEXT.md",
    "lessons_file": "LESSONS.md",
    "reference_systems_file": "REFERENCE_SYSTEMS.md",
    "summaries_dir": "summaries",
    "progress_dir": "progress",
    "test_reports_dir": "test-reports",
    "review_reports_dir": "review-reports",
    "growth_reports_dir": "growth-reports",
    "intake_reports_dir": "intake-reports",
}

PRODUCT_CONFIG_CANDIDATES = (
    "ael-workspace/project.yaml",
    "ael-workspace/config.yaml",
    ".buildrun-agent-engineering-lifecycle.yaml",
)

def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None or not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _normalize_project_config(data: dict[str, Any]) -> dict[str, Any]:
    if not ("product" in data or "workspace" in data or "planning" in data or "evidence" in data):
        return data

    product = data.get("product") or {}
    workspace = data.get("workspace") or {}
    planning = data.get("planning") or {}
    runs = data.get("runs") or {}
    knowledge = data.get("knowledge") or {}
    evidence = data.get("evidence") or {}
    ael = data.get("ael") or data.get("harness") or {}
    bmad = data.get("bmad") or {}

    workspace_root = str(workspace.get("root") or "ael-workspace").strip("/")
    planning_root = str(workspace.get("planning") or "planning").strip("/")
    runs_root = str(workspace.get("runs") or "runs").strip("/")
    knowledge_root = str(workspace.get("knowledge") or "knowledge").strip("/")
    evidence_root = str(workspace.get("evidence") or "evidence").strip("/")

    return {
        "phase0": {
            "root": f"{workspace_root}/{planning_root}",
            "product_specs": planning.get("product_specs") or "product-specs",
            "exec_plans_active": planning.get("exec_plans_active") or "exec-plans/active",
            "exec_plans_completed": planning.get("exec_plans_completed") or "exec-plans/completed",
            "tasks": planning.get("tasks") or "tasks",
            "agent_workspace": f"../{runs_root}/{runs.get('agent_workspace') or '.'}",
        },
        "bmad": {
            "install_root": bmad.get("install_root") or ".",
            "output_root": bmad.get("output_root") or f"{workspace_root}/bmad-output",
        },
        "harness": {
            "name": ael.get("name") or DEFAULT_HARNESS["name"],
            "profile": product.get("profile") or ael.get("profile") or DEFAULT_HARNESS["profile"],
            "product_name": product.get("name") or ael.get("product_name") or DEFAULT_HARNESS["product_name"],
            "product_id": product.get("id") or ael.get("product_id") or DEFAULT_HARNESS["product_id"],
            "task_contract": ael.get("task_contract") or DEFAULT_HARNESS["task_contract"],
            "knowledge_root": f"../{knowledge_root}",
            "evidence_root": f"../{evidence_root}",
            "context_file": knowledge.get("context_file") or DEFAULT_HARNESS["context_file"],
            "lessons_file": knowledge.get("lessons_file") or DEFAULT_HARNESS["lessons_file"],
            "reference_systems_file": knowledge.get("reference_systems_file") or DEFAULT_HARNESS["reference_systems_file"],
            "summaries_dir": evidence.get("summaries_dir") or DEFAULT_HARNESS["summaries_dir"],
            "progress_dir": evidence.get("progress_dir") or DEFAULT_HARNESS["progress_dir"],
            "test_reports_dir": evidence.get("test_reports_dir") or DEFAULT_HARNESS["test_reports_dir"],
            "review_reports_dir": evidence.get("review_reports_dir") or DEFAULT_HARNESS["review_reports_dir"],
            "growth_reports_dir": evidence.get("growth_reports_dir") or DEFAULT_HARNESS["growth_reports_dir"],
            "intake_reports_dir": evidence.get("intake_reports_dir") or DEFAULT_HARNESS["intake_reports_dir"],
        },
    }


def load_config(ael_root: Path, product_root: Path | None = None) -> dict[str, Any]:
    """Load buildrun-agent-engineering-lifecycle defaults, then overlay product workspace config when present."""
    harness_config_path = ael_root / ".ael/config.yaml"
    data = _normalize_project_config(_load_yaml(harness_config_path))
    if product_root is not None:
        for rel in PRODUCT_CONFIG_CANDIDATES:
            product_path = product_root / rel
            product_data = _normalize_project_config(_load_yaml(product_path))
            if product_data:
                data = {
                    **data,
                    "phase0": {**(data.get("phase0") or {}), **(product_data.get("phase0") or {})},
                    "bmad": {**(data.get("bmad") or {}), **(product_data.get("bmad") or {})},
                    "harness": {**(data.get("harness") or {}), **(product_data.get("harness") or {})},
                }
                break
    phase0 = {**DEFAULT_PHASE0, **(data.get("phase0") or {})}
    bmad = {**DEFAULT_BMAD, **(data.get("bmad") or {})}
    harness = {**DEFAULT_HARNESS, **(data.get("harness") or {})}
    return {"phase0": phase0, "bmad": bmad, "harness": harness}
