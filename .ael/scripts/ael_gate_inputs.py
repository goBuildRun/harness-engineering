#!/usr/bin/env python3
"""Dependency-scoped input fingerprints for AEL gates."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ael_gate_manifest import CandidateManifest, GateInputError
from ael_gate_tools import SHELL_TOOL_REF, tool_dependency_entries
from ael_runtime import canonical_digest


def execution_dependency_digest(adapter: Path, command: list[str]) -> str:
    """Digest the adapter, argv files, interpreters, wrappers, and imported helpers."""
    harness = Path(__file__).resolve().parents[2]
    adapter = adapter.resolve()
    candidates = CandidateManifest(harness, adapter.parent)
    entries = tool_dependency_entries(
        harness, [str(item) for item in command], candidates,
        execution_cwd=adapter.parent,
        bind_interpreter_environment=True,
        strict_external_python=True,
    )
    candidates.verify_fresh()
    return canonical_digest(entries)


def _manifest_inputs(harness: Path, candidates: CandidateManifest) -> str:
    manifest_path = harness / ".ael/ael-manifest.yaml"
    try:
        import yaml
    except ImportError:
        return candidates.digest(manifest_path, root=harness)
    try:
        payload = yaml.safe_load(candidates.read_text(manifest_path, root=harness)) or {}
    except yaml.YAMLError as exc:
        raise GateInputError("GATE_INPUT_MANIFEST_INVALID") from exc
    if not isinstance(payload, dict):
        raise GateInputError("GATE_INPUT_MANIFEST_INVALID")
    required_files = payload.get("required_files") or []
    if not isinstance(required_files, list) or not all(
        isinstance(rel, str) for rel in required_files
    ):
        raise GateInputError("GATE_INPUT_MANIFEST_INVALID")
    required = [
        (str(rel), candidates.digest(harness / str(rel), root=harness))
        for rel in required_files
    ]
    return canonical_digest({
        "manifest": candidates.digest(manifest_path, root=harness), "required": required,
        "agents": candidates.tree_digest(
            harness / ".ael/agents", allowed_root=harness, suffixes={".yaml", ".md"},
        ),
    })


def _safe_task_dir(product: Path, planning_credential: dict[str, Any]) -> Path | None:
    raw = str(planning_credential.get("task_dir") or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = product / candidate
    try:
        candidate = candidate.resolve()
        candidate.relative_to(product.resolve())
    except (OSError, ValueError) as exc:
        raise GateInputError("GATE_INPUT_SYMLINK_ESCAPE") from exc
    return candidate


def _work_item_id(planning_credential: dict[str, Any], explicit: str) -> str:
    work_item = planning_credential.get("work_item") or {}
    return str(explicit or (work_item.get("id") if isinstance(work_item, dict) else "") or "").strip()


def _task_ids(
    candidates: CandidateManifest, task_dir: Path | None, task_id: str, work_item_id: str,
) -> set[str]:
    values = {value for value in (task_id, work_item_id, task_dir.name if task_dir else "") if value}
    if task_dir is None:
        return values
    plan = task_dir / "03-实施方案.md"
    text = candidates.read_text(plan, root=candidates.product)
    if not text:
        return values
    try:
        from task_contract_check import parse_task_rows
        rows, _issues = parse_task_rows(text)
    except (ImportError, ValueError):
        rows = []
    values.update(str(row.get("id") or "").strip("` ") for row in rows)
    values.discard("")
    return values


def _qa_task_inputs(
    harness: Path, product: Path, task_dir: Path | None, task_ids: set[str],
    work_item_id: str, candidates: CandidateManifest,
) -> dict[str, str]:
    from workspace_paths import load_layout

    layout = load_layout(harness, product)
    report_paths: set[Path] = set()
    task_name = task_dir.name if task_dir else ""
    for task_id in task_ids:
        for root, suffix in (
            (layout.test_reports_dir, "TEST"),
            (layout.review_reports_dir, "REVIEW"),
        ):
            names = []
            if work_item_id:
                names.extend((f"{work_item_id}-{task_id}-{suffix}.md", f"{work_item_id}-{suffix}.md"))
            names.append(f"{task_id}-{suffix}.md")
            if task_name:
                names.extend((f"{task_name}-{task_id}-{suffix}.md", f"{task_name}-{suffix}.md"))
            report_paths.update(root / name for name in names)

    receipt_paths = {
        layout.runs_root / "active_task.json",
        layout.runs_root / "planning_gate_pass.json",
        layout.runs_root / "phase0_pass.json",
    }
    receipt_paths.update(layout.runs_root / f"qa_approved_{item}.json" for item in task_ids)
    task_roots = {layout.runs_root / "tasks" / task_id for task_id in task_ids}
    receipt_digests = [
        candidates.tree_digest(root, allowed_root=product, suffixes={".json"})
        for root in sorted(task_roots) if root.is_dir()
    ]
    receipt_digests.append(candidates.selected_digest(receipt_paths, root=product))
    return {
        "evidence": candidates.selected_digest(report_paths, root=product),
        "receipts": canonical_digest(receipt_digests),
    }


def _growth_task_digest(
    root: Path, task_ids: set[str], work_item_id: str, candidates: CandidateManifest,
) -> str:
    candidates._guard_path(root, candidates.product)
    if not root.is_dir():
        return "absent"
    selected: set[Path] = set()
    filename_ids = {work_item_id} if work_item_id else task_ids
    markers = (
        f"**Work Item**：`{work_item_id}`",
        f"Work Item: `{work_item_id}`",
        f"Work Item：`{work_item_id}`",
    ) if work_item_id else ()
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in candidates.children(directory, allowed_root=candidates.product):
            if path.is_dir():
                if path.is_symlink():
                    raise GateInputError("GATE_INPUT_SYMLINK_DIRECTORY")
                pending.append(path)
                continue
            if not path.is_file() or path.suffix != ".md":
                continue
            if not filename_ids or any(
                re.search(rf"(^|[-_.]){re.escape(task_id)}(?=$|[-_.])", path.name)
                for task_id in filename_ids
            ):
                selected.add(path)
                continue
            if markers and any(
                marker in candidates.read_text(path, root=candidates.product)[:2000]
                for marker in markers
            ):
                selected.add(path)
    return candidates.selected_digest(selected, root=candidates.product)


def gate_input_digest(
    name: str,
    *,
    harness: Path,
    product: Path,
    changed_files: list[str],
    planning_gate: Path,
    planning_credential: dict[str, Any],
    command: list[str],
    task_id: str = "",
    work_item_id: str = "",
    candidates_manifest: CandidateManifest | None = None,
    deadline: float | None = None,
) -> str:
    candidates = candidates_manifest or CandidateManifest(
        harness, product, deadline=deadline,
    )
    candidates.check_deadline()
    task_dir = _safe_task_dir(product, planning_credential)
    current_work_item = _work_item_id(planning_credential, work_item_id)
    current_task_ids = _task_ids(candidates, task_dir, task_id, current_work_item)
    changed_paths = sorted(set(changed_files))
    changed_content = [
        (rel, candidates.digest(product / rel, root=product)) for rel in changed_paths
        if not rel.startswith("ael-workspace/runs/")
    ]
    project = product / "ael-workspace/project.yaml"
    common = {
        "gate": name,
        "tools": tool_dependency_entries(
            harness, command, candidates, bind_interpreter_environment=True,
            dynamic_import_callback=lambda: candidates.mark_gate_uncacheable(name),
        ),
    }
    if name == "harness":
        value: Any = {
            **common,
            "manifest_inputs": _manifest_inputs(harness, candidates),
            "config": candidates.digest(harness / ".ael/config.yaml", root=harness),
        }
    elif name == "structure":
        value = {
            **common, "paths": changed_paths, "project": candidates.digest(project, root=product),
            "rules": candidates.tree_digest(
                harness / ".ael/rules", allowed_root=harness, suffixes={".md", ".yaml"},
            ),
        }
    elif name == "diff_integrity":
        value = {**common, "changed": changed_content}
    elif name in {"plan_sync", "dag_sync"}:
        value = {
            **common, "paths": changed_paths,
            "plan": candidates.digest(task_dir / "03-实施方案.md", root=product) if task_dir else "absent",
            "dag": candidates.digest(task_dir / "tasks-dag.md", root=product) if task_dir else "absent",
            "project": candidates.digest(project, root=product),
        }
    elif name == "qa_evidence":
        scoped = _qa_task_inputs(
            harness, product, task_dir, current_task_ids, current_work_item, candidates,
        )
        value = {
            **common, "changed": changed_content,
            "plan": candidates.digest(task_dir / "03-实施方案.md", root=product) if task_dir else "absent",
            **scoped,
        }
    elif name == "knowledge":
        value = {
            **common,
            "planning": candidates.tree_digest(
                task_dir, allowed_root=product, suffixes={".md", ".json"},
            ) if task_dir else "absent",
            "context": candidates.digest(
                product / "ael-workspace/knowledge/CONTEXT.md", root=product,
            ),
            "project": candidates.digest(project, root=product),
        }
    elif name.startswith("growth_"):
        from workspace_paths import load_layout
        layout = load_layout(harness, product)
        value = {
            **common,
            "evidence": canonical_digest([
                _growth_task_digest(root, current_task_ids, current_work_item, candidates)
                for root in (
                    layout.summaries_dir, layout.progress_dir,
                    layout.test_reports_dir, layout.review_reports_dir,
                )
            ]),
            "growth": _growth_task_digest(
                layout.growth_reports_dir, current_task_ids, current_work_item, candidates,
            ),
        }
    elif name.startswith("quality_"):
        value = {
            **common, "changed": changed_content,
            "project": candidates.digest(project, root=product),
        }
    else:
        value = {
            **common, "changed": changed_content,
            "planning_gate": candidates.digest(planning_gate, root=product),
        }
    candidates.check_deadline()
    return canonical_digest(value)
