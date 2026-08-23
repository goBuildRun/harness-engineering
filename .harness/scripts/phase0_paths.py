#!/usr/bin/env python3
"""Compatibility path resolver for product Harness workspaces.

Preferred new code should import ``workspace_paths``. This module keeps the
historical ``phase0_*`` API so existing product workspaces and older scripts can
continue to run while the public concept moves to Product Workspace + BMAD
Planning + Planning Gate.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_output import dump_json
from harness_task_resolution import valid_task_id
from product_context import ProductContextError, resolve_product_context
from workspace_config import DEFAULT_HARNESS, load_config

LEGACY_SPEC_PREFIX = "docs/product-specs/"
LEGACY_EXEC_PREFIX = "docs/exec-plans/active/"
LEGACY_EXEC_COMPLETED_PREFIX = "docs/exec-plans/completed/"


@dataclass(frozen=True)
class Phase0Layout:
    product_root: Path
    harness_root: Path
    phase0_root: Path
    product_specs: Path
    exec_plans_active: Path
    exec_plans_completed: Path
    tasks: Path
    task_templates: Path
    agent_workspace: Path
    knowledge_root: Path
    context_file: Path
    lessons_file: Path
    reference_systems_file: Path
    summaries_dir: Path
    progress_dir: Path
    test_reports_dir: Path
    review_reports_dir: Path
    growth_reports_dir: Path
    intake_reports_dir: Path
    bmad_install_root: Path
    bmad_output_root: Path
    harness_name: str
    product_id: str
    harness_profile: str
    product_name: str
    task_contract: str
    workspace_root: Path
    planning_root: Path
    runs_root: Path
    evidence_root: Path

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.product_root)).replace("\\", "/")
        except ValueError:
            return str(path)

    def rel_phase0(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.phase0_root)).replace("\\", "/")
        except ValueError:
            return self.rel(path)

    def tasks_git_prefix(self) -> str:
        return f"{self.rel(self.tasks)}/"

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_schema": "product-workspace-v1",
            "product_root": str(self.product_root),
            "harness_root": str(self.harness_root),
            "planning_root": str(self.planning_root),
            "runs_root": str(self.runs_root),
            "evidence_root": str(self.evidence_root),
            "workspace_root": str(self.workspace_root),
            "planning_root_rel": self.rel(self.planning_root),
            "runs_root_rel": self.rel(self.runs_root),
            "evidence_root_rel": self.rel(self.evidence_root),
            "workspace_root_rel": self.rel(self.workspace_root),
            "planning_gate_pass": str(self.runs_root / "planning_gate_pass.json"),
            "legacy_phase0_pass": str(self.runs_root / "phase0_pass.json"),
            "phase0_root": str(self.phase0_root),
            "product_specs": str(self.product_specs),
            "exec_plans_active": str(self.exec_plans_active),
            "exec_plans_completed": str(self.exec_plans_completed),
            "tasks": str(self.tasks),
            "task_templates": str(self.task_templates),
            "agent_workspace": str(self.agent_workspace),
            "knowledge_root": str(self.knowledge_root),
            "context_file": str(self.context_file),
            "lessons_file": str(self.lessons_file),
            "reference_systems_file": str(self.reference_systems_file),
            "summaries_dir": str(self.summaries_dir),
            "progress_dir": str(self.progress_dir),
            "test_reports_dir": str(self.test_reports_dir),
            "review_reports_dir": str(self.review_reports_dir),
            "growth_reports_dir": str(self.growth_reports_dir),
            "intake_reports_dir": str(self.intake_reports_dir),
            "bmad_install_root": str(self.bmad_install_root),
            "bmad_output_root": str(self.bmad_output_root),
            "harness_name": self.harness_name,
            "product_id": self.product_id,
            "harness_profile": self.harness_profile,
            "product_name": self.product_name,
            "task_contract": self.task_contract,
            "phase0_root_rel": self.rel(self.phase0_root),
            "tasks_git_prefix": self.tasks_git_prefix(),
        }


def harness_root_from_script() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def resolve_product_root(harness_root: Path, product_root: Path | None = None, product_id: str = "") -> Path:
    if product_root is not None:
        return product_root.resolve()
    return resolve_product_context(harness_root, product_id=product_id).root


def _join_under(base: Path, rel: str) -> Path:
    rel = rel.strip().replace("\\", "/").lstrip("/")
    return (base / rel).resolve()


def load_layout(harness_root: Path | None = None, product_root: Path | None = None, product_id: str = "") -> Phase0Layout:
    hr = (harness_root or harness_root_from_script()).resolve()
    mr = resolve_product_root(hr, product_root, product_id)
    cfg = load_config(hr, mr)
    p0 = cfg["phase0"]
    bmad = cfg["bmad"]
    harness = cfg["harness"]
    phase0_root = _join_under(mr, str(p0["root"]))
    knowledge_root = _join_under(phase0_root, str(harness["knowledge_root"]))
    evidence_root = _join_under(phase0_root, str(harness.get("evidence_root") or harness["knowledge_root"]))
    agent_workspace = _join_under(phase0_root, str(p0["agent_workspace"]))
    return Phase0Layout(
        product_root=mr,
        harness_root=hr,
        phase0_root=phase0_root,
        product_specs=_join_under(phase0_root, str(p0["product_specs"])),
        exec_plans_active=_join_under(phase0_root, str(p0["exec_plans_active"])),
        exec_plans_completed=_join_under(phase0_root, str(p0["exec_plans_completed"])),
        tasks=_join_under(phase0_root, str(p0["tasks"])),
        task_templates=hr / "tasks" / "_templates",
        agent_workspace=agent_workspace,
        knowledge_root=knowledge_root,
        context_file=_join_under(knowledge_root, str(harness["context_file"])),
        lessons_file=_join_under(knowledge_root, str(harness["lessons_file"])),
        reference_systems_file=_join_under(knowledge_root, str(harness["reference_systems_file"])),
        summaries_dir=_join_under(evidence_root, str(harness["summaries_dir"])),
        progress_dir=_join_under(evidence_root, str(harness["progress_dir"])),
        test_reports_dir=_join_under(evidence_root, str(harness["test_reports_dir"])),
        review_reports_dir=_join_under(evidence_root, str(harness["review_reports_dir"])),
        growth_reports_dir=_join_under(evidence_root, str(harness["growth_reports_dir"])),
        intake_reports_dir=_join_under(evidence_root, str(harness["intake_reports_dir"])),
        bmad_install_root=_join_under(mr, str(bmad.get("install_root") or ".")),
        bmad_output_root=_join_under(mr, str(bmad.get("output_root") or "harness-workspace/bmad-output")),
        harness_name=str(harness.get("name") or DEFAULT_HARNESS["name"]),
        product_id=str(harness.get("product_id") or DEFAULT_HARNESS["product_id"]),
        harness_profile=str(harness.get("profile") or DEFAULT_HARNESS["profile"]),
        product_name=str(harness.get("product_name") or DEFAULT_HARNESS["product_name"]),
        task_contract=str(harness.get("task_contract") or DEFAULT_HARNESS["task_contract"]),
        workspace_root=_join_under(mr, str(p0["root"])).parent,
        planning_root=phase0_root,
        runs_root=agent_workspace,
        evidence_root=evidence_root,
    )


def normalize_legacy_rel(rel: str) -> str:
    rel = rel.strip().strip("`").replace("\\", "/")
    if rel.startswith(LEGACY_SPEC_PREFIX):
        return rel[len(LEGACY_SPEC_PREFIX) :]
    if rel.startswith(LEGACY_EXEC_PREFIX):
        return rel[len(LEGACY_EXEC_PREFIX) :]
    if rel.startswith(LEGACY_EXEC_COMPLETED_PREFIX):
        return rel[len(LEGACY_EXEC_COMPLETED_PREFIX) :]
    return rel.lstrip("./")


def resolve_phase0_path(layout: Phase0Layout, rel: str) -> Path:
    rel = normalize_legacy_rel(rel)
    if not rel:
        return layout.phase0_root
    path = Path(rel)
    if path.is_absolute():
        return path.resolve()
    # monorepo 根相对：phase0/product-specs/foo.md
    phase0_name = layout.phase0_root.name
    if rel.startswith(f"{phase0_name}/"):
        return (layout.product_root / rel).resolve()
    mr_candidate = (layout.product_root / rel).resolve()
    if mr_candidate.is_file() or mr_candidate.is_dir():
        try:
            mr_candidate.relative_to(layout.phase0_root)
            return mr_candidate
        except ValueError:
            pass
    return (layout.phase0_root / rel).resolve()


def resolve_task_dir(layout: Phase0Layout, value: str, *, cwd: Path | None = None) -> Path:
    """Resolve absolute, product-relative, planning-relative, or task-root-relative task paths."""
    raw = value.strip().strip("`").replace("\\", "/")
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()

    candidates: list[Path] = []
    if cwd is not None:
        candidates.append((cwd / path).resolve())
    candidates.extend(
        [
            (layout.product_root / path).resolve(),
            (layout.phase0_root / path).resolve(),
            (layout.tasks / path).resolve(),
        ]
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    product_tasks = layout.rel(layout.tasks).rstrip("/")
    planning_tasks = layout.rel_phase0(layout.tasks).rstrip("/")
    if raw == product_tasks or raw.startswith(f"{product_tasks}/"):
        return (layout.product_root / path).resolve()
    if raw == planning_tasks or raw.startswith(f"{planning_tasks}/"):
        return (layout.phase0_root / path).resolve()
    return (layout.tasks / path).resolve()


def planning_gate_path(layout: Phase0Layout) -> Path:
    return layout.runs_root / "planning_gate_pass.json"


def legacy_phase0_path(layout: Phase0Layout) -> Path:
    return layout.runs_root / "phase0_pass.json"


def ci_planning_gate_path(layout: Phase0Layout, task_id: str) -> Path:
    if not valid_task_id(task_id):
        return layout.runs_root / "ci" / "_invalid" / "planning_gate_pass.json"
    return layout.runs_root / "ci" / task_id / "planning_gate_pass.json"


def active_planning_gate_path(layout: Phase0Layout) -> Path:
    ci_task_id = os.environ.get("HARNESS_CI_TASK_ID", "").strip()
    ci_gate = os.environ.get("HARNESS_CI_PLANNING_GATE", "").strip()
    if ci_task_id or ci_gate:
        expected = ci_planning_gate_path(layout, ci_task_id).resolve()
        if not ci_task_id or not ci_gate:
            return layout.runs_root / "ci" / "_invalid" / "planning_gate_pass.json"
        candidate = Path(ci_gate)
        if not candidate.is_absolute():
            candidate = layout.product_root / candidate
        try:
            candidate = candidate.resolve()
            candidate.relative_to((layout.runs_root / "ci").resolve())
        except (OSError, ValueError):
            return layout.runs_root / "ci" / "_invalid" / "planning_gate_pass.json"
        if candidate != expected:
            return layout.runs_root / "ci" / "_invalid" / "planning_gate_pass.json"
        return expected
    task_id = os.environ.get("HARNESS_TASK_ID", "").strip()
    if task_id:
        if not valid_task_id(task_id):
            return layout.runs_root / "tasks" / "_invalid" / "planning_gate_pass.json"
        return layout.runs_root / "tasks" / task_id / "planning_gate_pass.json"
    preferred = planning_gate_path(layout)
    return preferred if preferred.is_file() else legacy_phase0_path(layout)


def load_active_planning_gate(layout: Phase0Layout) -> dict[str, Any]:
    path = active_planning_gate_path(layout)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def ensure_phase0_dirs(layout: Phase0Layout) -> None:
    for d in (
        layout.product_specs,
        layout.exec_plans_active,
        layout.exec_plans_completed,
        layout.tasks,
        layout.agent_workspace,
        layout.agent_workspace / "tasks",
        layout.knowledge_root,
        layout.summaries_dir,
        layout.progress_dir,
        layout.test_reports_dir,
        layout.review_reports_dir,
        layout.growth_reports_dir,
        layout.intake_reports_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default="")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--product-id", default="")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("json")
    p_ensure = sub.add_parser("ensure-dirs")
    p_ensure.add_argument("--harness-root", default="")
    p_ensure.add_argument("--product-root", default="")
    p_ensure.add_argument("--product-id", default="")

    args = parser.parse_args()
    hr = Path(args.harness_root).resolve() if args.harness_root else harness_root_from_script()
    product_root_arg = args.product_root
    mr = Path(product_root_arg).resolve() if product_root_arg else None
    try:
        layout = load_layout(hr, mr, args.product_id)
    except ProductContextError as exc:
        dump_json({"decision": "block", "reason": f"PRODUCT_CONTEXT_ERROR: {exc}"})
        return 0

    if args.cmd == "ensure-dirs":
        ensure_phase0_dirs(layout)
        dump_json(
            {
                "decision": "pass",
                "planning_root": layout.rel(layout.planning_root),
                "workspace_root": layout.rel(layout.workspace_root),
                "phase0_root": layout.rel(layout.phase0_root),
            }
        )
        return 0

    dump_json(layout.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
