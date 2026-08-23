#!/usr/bin/env python3
"""Shared QA mechanical bundle and per-task digest-bound receipt metadata."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from harness_gate_execution import gate_input_digest
from harness_output import dump_json
from harness_runtime import canonical_digest, load_result, now, policy_for, subject_for
from harness_task_resolution import valid_task_id
from qa_evidence_check import (
    first_existing, implementer_session_from_result, report_candidates,
    validate_reviewer_identity,
)
from workspace_paths import active_planning_gate_path, load_active_planning_gate, load_layout
from worktree_baseline import changed_since_baseline
from process_control import run_process_group


BUNDLE_SCHEMA = "harness-qa-mechanical-bundle-v1"
RECEIPT_SCHEMA = "harness-qa-receipt-v2"


def _sha(path: Path | None) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path else "absent"
    except OSError:
        return "absent"


def _active_id(layout) -> str:
    ci_task_id = os.environ.get("HARNESS_CI_TASK_ID", "").strip()
    if ci_task_id:
        if not valid_task_id(ci_task_id):
            return ""
        gate = load_active_planning_gate(layout) or {}
        work_item = gate.get("work_item") or {}
        if isinstance(work_item, dict):
            return str(work_item.get("id") or ci_task_id).strip()
        return ci_task_id
    try:
        data = json.loads((layout.runs_root / "active_task.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("work_item_id") or data.get("task_id") or "").strip()


def current_binding(harness: Path, product: Path, work_item_id: str, paths: list[str]) -> dict[str, Any]:
    layout = load_layout(harness, product)
    active = _active_id(layout)
    if active != work_item_id or not valid_task_id(work_item_id):
        raise ValueError("QA_ACTIVE_TASK_MISMATCH")
    task_root = layout.runs_root / "tasks" / work_item_id
    baseline = task_root / "worktree_baseline.json"
    if not baseline.is_file():
        raise ValueError("QA_BASELINE_MISSING")
    changed = changed_since_baseline(product, baseline)
    normalized_paths = sorted(set(paths))
    if normalized_paths != changed:
        raise ValueError("QA_PATHS_STALE")
    result_path = task_root / "result.json"
    try:
        result = load_result(result_path)
    except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("QA_RESULT_INVALID") from exc
    if result.get("task_id") != work_item_id:
        raise ValueError("QA_RESULT_TASK_MISMATCH")
    implementer_session = implementer_session_from_result(result)
    gate = load_active_planning_gate(layout) or {}
    planning_gate = active_planning_gate_path(layout)
    scripts = harness / ".harness/scripts"
    mechanical_inputs = {
        name: gate_input_digest(
            name, harness=harness, product=product, changed_files=normalized_paths,
            planning_gate=planning_gate, planning_credential=gate, command=command,
        )
        for name, command in {
            "structure": ["bash", str(scripts / "structure_guard.sh"), "--diff"],
            "plan_sync": ["bash", str(scripts / "plan_sync_check.sh")],
        }.items()
    }
    return {
        "work_item_id": work_item_id,
        "subject_digest": subject_for(product, changed),
        "policy_digest": policy_for(harness, product),
        "paths_reviewed": normalized_paths,
        "paths_digest": canonical_digest(normalized_paths),
        "implementer_session_id": implementer_session,
        "mechanical_inputs": mechanical_inputs,
    }


def bundle_payload(binding: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema": BUNDLE_SCHEMA,
        "work_item_id": binding["work_item_id"],
        "subject_digest": binding["subject_digest"],
        "policy_digest": binding["policy_digest"],
        "paths_digest": binding["paths_digest"],
        "structure_gate": {
            "decision": "pass", "input_digest": binding["mechanical_inputs"]["structure"],
        },
        "plan_sync_gate": {
            "decision": "pass", "input_digest": binding["mechanical_inputs"]["plan_sync"],
        },
    }
    payload["bundle_digest"] = canonical_digest(payload)
    return payload


def bundle_path(layout, work_item_id: str, bundle_digest: str) -> Path:
    return layout.runs_root / "tasks" / work_item_id / f"qa_bundle_{bundle_digest}.json"


def load_valid_bundle(layout, binding: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    expected = bundle_payload(binding)
    path = bundle_path(layout, binding["work_item_id"], expected["bundle_digest"])
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, expected
    return (path if existing == expected else None), expected


def host_reviewer_identity(work_item_id: str, task_id: str) -> dict[str, Any]:
    path = Path(os.environ.get("HARNESS_QA_REVIEWER_IDENTITY_RECEIPT", "").strip())
    if str(path) == "." or not path.is_file():
        raise ValueError("QA_HOST_REVIEWER_IDENTITY_MISSING")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("QA_HOST_REVIEWER_IDENTITY_INVALID") from exc
    if not isinstance(payload, dict):
        raise ValueError("QA_HOST_REVIEWER_IDENTITY_INVALID")
    return payload


def _write_bundle(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _run_mechanical_gates(harness: Path, product: Path) -> dict[str, Any]:
    scripts = harness / ".harness/scripts"
    env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product), "HARNESS_GATE_READ_ONLY": "1"}
    for name, command in (
        ("structure", ["bash", str(scripts / "structure_guard.sh"), "--diff"]),
        ("plan_sync", ["bash", str(scripts / "plan_sync_check.sh")]),
    ):
        try:
            completed = run_process_group(command, cwd=harness, env=env, timeout=120)
            payload = json.loads(completed.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return {"decision": "block", "reason": f"QA_MECHANICAL_{name.upper()}_INVALID"}
        if completed.returncode != 0 or payload.get("decision") != "pass":
            return {"decision": "block", "reason": f"QA_MECHANICAL_{name.upper()}_BLOCK"}
    return {"decision": "pass", "reason": "QA_MECHANICAL_GATES_PASS"}


def prepare_bundle(
    harness: Path, product: Path, work_item_id: str, paths: list[str],
    gate_runner: Callable[[Path, Path], dict[str, Any]] = _run_mechanical_gates,
) -> dict[str, Any]:
    layout = load_layout(harness, product)
    if not valid_task_id(work_item_id):
        raise ValueError("QA_ACTIVE_TASK_MISMATCH")
    task_root = layout.runs_root / "tasks" / work_item_id
    task_root.mkdir(parents=True, exist_ok=True)
    lock_path = task_root / "qa-bundle.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        binding = current_binding(harness, product, work_item_id, paths)
        existing, payload = load_valid_bundle(layout, binding)
        if existing is not None:
            return {
                "decision": "pass", "reason": "QA_BUNDLE_REUSED",
                "bundle_ref": layout.rel(existing), "bundle": payload,
            }
        gate_result = gate_runner(harness, product)
        if gate_result.get("decision") != "pass":
            return gate_result
        current = current_binding(harness, product, work_item_id, paths)
        existing, current_payload = load_valid_bundle(layout, current)
        if existing is not None:
            return {
                "decision": "pass", "reason": "QA_BUNDLE_REUSED",
                "bundle_ref": layout.rel(existing), "bundle": current_payload,
            }
        if current_payload != payload:
            return {"decision": "block", "reason": "QA_BUNDLE_INPUT_CHANGED"}
        path = bundle_path(layout, work_item_id, payload["bundle_digest"])
        _write_bundle(path, payload)
        return {
            "decision": "pass", "reason": "QA_BUNDLE_PREPARED",
            "bundle_ref": layout.rel(path), "bundle": payload,
        }


def bundle_status(harness: Path, product: Path, work_item_id: str, paths: list[str]) -> dict[str, Any]:
    layout = load_layout(harness, product)
    binding = current_binding(harness, product, work_item_id, paths)
    existing, payload = load_valid_bundle(layout, binding)
    return {
        "decision": "pass" if existing else "block",
        "reason": "QA_BUNDLE_CURRENT" if existing else "QA_BUNDLE_REQUIRED",
        "bundle_ref": layout.rel(existing) if existing else "",
        "bundle": payload,
    }


def receipt_binding(
    harness: Path, product: Path, work_item_id: str, task_id: str,
    paths: list[str], reviewer_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not valid_task_id(task_id):
        return {"decision": "block", "reason": "QA_TASK_ID_INVALID"}
    layout = load_layout(harness, product)
    binding = current_binding(harness, product, work_item_id, paths)
    try:
        reviewer_identity = reviewer_identity or host_reviewer_identity(work_item_id, task_id)
    except ValueError as exc:
        return {"decision": "block", "reason": str(exc)}
    reviewer_session_id, identity_issue = validate_reviewer_identity(
        reviewer_identity, work_item_id=work_item_id, task_id=task_id,
        harness_root=harness,
    )
    if identity_issue:
        return {"decision": "block", "reason": identity_issue}
    bundle, expected = load_valid_bundle(layout, binding)
    if bundle is None:
        return {"decision": "block", "reason": "QA_BUNDLE_REQUIRED"}
    implementer = binding["implementer_session_id"]
    independent = (
        reviewer_session_id != "unknown"
        and implementer != "unknown"
        and reviewer_session_id != implementer
    )
    if not independent:
        return {"decision": "block", "reason": "QA_INDEPENDENCE_UNPROVEN"}
    gate = load_active_planning_gate(layout) or {}
    raw_task_dir = str(gate.get("task_dir") or "")
    task_dir = Path(raw_task_dir)
    if not task_dir.is_absolute():
        task_dir = product / task_dir
    test = first_existing(report_candidates(
        layout.test_reports_dir, work_item_id, task_dir, task_id, "TEST",
    ))
    review = first_existing(report_candidates(
        layout.review_reports_dir, work_item_id, task_dir, task_id, "REVIEW",
    ))
    if test is None or review is None:
        return {"decision": "block", "reason": "QA_REPORTS_REQUIRED_BEFORE_SIGNOFF"}
    return {
        "decision": "pass", "reason": "QA_RECEIPT_BINDING_READY",
        "binding": {
            "schema": RECEIPT_SCHEMA,
            "subject_digest": binding["subject_digest"],
            "policy_digest": binding["policy_digest"],
            "paths_digest": binding["paths_digest"],
            "bundle_ref": layout.rel(bundle),
            "bundle_digest": expected["bundle_digest"],
            "reviewer_session_id": reviewer_session_id,
            "reviewer_identity_receipt": reviewer_identity,
            "implementer_session_id": implementer,
            "independent": True,
            "test_ref": layout.rel(test),
            "test_digest": _sha(test),
            "review_ref": layout.rel(review),
            "review_digest": _sha(review),
            "bound_at": now(),
        },
    }


def _paths(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("QA_PATHS_INVALID") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("QA_PATHS_INVALID")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "prepare", "binding"))
    parser.add_argument("--harness-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--product-root", default=os.environ.get("HARNESS_PRODUCT_ROOT", ""))
    parser.add_argument("--work-item", required=True)
    parser.add_argument("--task-id", default="")
    parser.add_argument("--paths-json", required=True)
    args = parser.parse_args()
    try:
        paths = _paths(args.paths_json)
        common = (Path(args.harness_root).resolve(), Path(args.product_root).resolve(), args.work_item, paths)
        if args.command == "status":
            result = bundle_status(*common)
        elif args.command == "prepare":
            result = prepare_bundle(*common)
        else:
            result = receipt_binding(
                common[0], common[1], common[2], args.task_id,
                common[3],
            )
    except (OSError, ValueError) as exc:
        result = {"decision": "block", "reason": str(exc)}
    dump_json(result)
    return 0 if result.get("decision") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
