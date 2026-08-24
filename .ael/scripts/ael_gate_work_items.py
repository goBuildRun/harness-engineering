#!/usr/bin/env python3
"""Work-item resolution helpers used by AEL gate planning."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ael_runtime import canonical_digest
from ael_task_resolution import valid_task_id
from workspace_paths import load_layout


CI_PLANNING_SCHEMA = "harness-ci-planning-environment-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _ci_credential_target(layout, task_id: str) -> Path | None:
    runs_root = layout.runs_root.resolve()
    try:
        runs_root.relative_to(layout.product_root.resolve())
    except (OSError, ValueError):
        return None
    current = runs_root
    for component in ("ci", task_id):
        current = current / component
        if current.is_symlink():
            return None
    target = current / "planning_gate_pass.json"
    try:
        target.parent.resolve(strict=False).relative_to(runs_root)
    except (OSError, ValueError):
        return None
    return target


def prepare_ci_task(
    harness: Path, product: Path, task_id: str, *, changed_files: list[str] | None = None,
) -> bool:
    if changed_files is not None and not all(isinstance(path, str) for path in changed_files):
        return False
    changed = list(dict.fromkeys(changed_files or []))
    binding = _committed_task_resolution(harness, product, task_id)
    if binding["status"] != "unique":
        return False
    layout = load_layout(harness, product)
    task_dir = Path(str(binding["task_dir"])).resolve()
    source = next(
        (path for path in (task_dir / "planning_gate_pass.json", task_dir / "phase0_pass.json")
         if path.is_file()),
        None,
    )
    if source is None:
        return False
    try:
        credential = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(credential, dict) or credential.get("decision") != "pass":
        return False
    resolved_work_item = binding["work_item"] or {}
    source_work_item = credential.get("work_item") or {}
    if not isinstance(source_work_item, dict):
        source_work_item = {}
    credential["ci_task_id"] = task_id
    credential["task_dir"] = str(task_dir.resolve())
    credential["work_item"] = {**source_work_item, **resolved_work_item}
    try:
        source_ref = str(source.resolve().relative_to(product.resolve())).replace("\\", "/")
        source_digest = _sha256(source)
    except (OSError, ValueError):
        return False
    credential.update({
        "schema": CI_PLANNING_SCHEMA,
        "committed_source_ref": source_ref,
        "committed_source_digest": source_digest,
        "changed_files": changed,
        "changed_files_digest": canonical_digest(changed),
    })
    target = _ci_credential_target(layout, task_id)
    if target is None:
        return False
    try:
        _write_json_atomic(target, credential)
    except OSError:
        return False
    return True


def _task_work_item_resolution(task_dir: Path) -> dict[str, Any]:
    candidates = []
    for name in ("task.json", "planning_gate_pass.json", "phase0_pass.json"):
        try:
            data = json.loads((task_dir / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value = data.get("work_item")
        if isinstance(value, dict) and value.get("id"):
            candidates.append({"source": f"{task_dir.name}/{name}", "id": str(value["id"]),
                               "provider": str(value.get("provider") or "")})
        elif isinstance(value, str) and value:
            candidates.append({"source": f"{task_dir.name}/{name}", "id": value, "provider": ""})
    candidates.sort(key=lambda item: item["source"])
    ids = {item["id"] for item in candidates}
    providers = {item["provider"] for item in candidates if item["provider"]}
    if len(ids) == 1 and len(providers) <= 1:
        return {"status": "unique", "work_item": {"id": next(iter(ids)),
                "provider": next(iter(providers), "")}, "candidates": candidates}
    status = "ambiguous" if candidates else "missing"
    return {"status": status, "work_item": None, "candidates": candidates}


def _task_work_item(task_dir: Path) -> dict[str, str] | None:
    return _task_work_item_resolution(task_dir)["work_item"]


def _committed_task_resolution(harness: Path, product: Path, task_id: str) -> dict[str, Any]:
    if not valid_task_id(task_id):
        return {"status": "missing", "work_item": None, "candidates": [], "task_dir": ""}
    tasks = load_layout(harness, product).tasks
    if not tasks.is_dir():
        return {"status": "missing", "work_item": None, "candidates": [], "task_dir": ""}
    tasks_root = tasks.resolve()
    records = [
        (task_dir, resolution)
        for task_dir in sorted(tasks.iterdir())
        if task_dir.is_dir()
        and task_dir.resolve().is_relative_to(tasks_root)
        and (resolution := _task_work_item_resolution(task_dir))["candidates"]
    ]
    direct = next((value for task_dir, value in records if task_dir.name == task_id), None)
    target_ids = {task_id, *(item["id"] for item in (direct or {}).get("candidates", []))}
    matches = [(task_dir, value) for task_dir, value in records if task_dir.name == task_id
               or any(item["id"] in target_ids for item in value["candidates"])]
    candidates = sorted((item for _task_dir, value in matches for item in value["candidates"]),
                        key=lambda item: item["source"])
    if len(matches) == 1 and matches[0][1]["status"] == "unique":
        return {"status": "unique", "work_item": matches[0][1]["work_item"],
                "candidates": candidates, "task_dir": str(matches[0][0].resolve())}
    status = "ambiguous" if matches else "missing"
    return {"status": status, "work_item": None, "candidates": candidates, "task_dir": ""}


def committed_work_item_resolution(harness: Path, product: Path, task_id: str) -> dict[str, Any]:
    resolution = _committed_task_resolution(harness, product, task_id)
    return {key: value for key, value in resolution.items() if key != "task_dir"}


def committed_work_item(harness: Path, product: Path, task_id: str) -> dict[str, str] | None:
    return committed_work_item_resolution(harness, product, task_id)["work_item"]


def committed_task_binding(harness: Path, product: Path, task_id: str) -> dict[str, Any]:
    return _committed_task_resolution(harness, product, task_id)


def validate_planning_credential(
    harness: Path, product: Path, task_id: str, credential: dict[str, Any], *,
    work_item_id: str = "", provider: str = "", require_ci_task_id: bool = False,
    changed_files: list[str] | None = None,
) -> dict[str, str]:
    if credential.get("decision") != "pass":
        return {"decision": "block", "reason": "CI_PLANNING_CREDENTIAL_DECISION_INVALID"}
    layout = load_layout(harness, product)
    binding = _committed_task_resolution(harness, product, task_id)
    if binding["status"] != "unique" and work_item_id and work_item_id != task_id:
        binding = _committed_task_resolution(harness, product, work_item_id)
    if binding["status"] != "unique" and credential.get("source") == "harness-plan-batch":
        raw_task_dir = str(credential.get("task_dir") or "").strip()
        candidate = Path(raw_task_dir)
        if not candidate.is_absolute():
            candidate = product / candidate
        try:
            candidate = candidate.resolve()
            candidate.relative_to(layout.tasks.resolve())
        except (OSError, ValueError):
            candidate = None
        current_work_item = credential.get("work_item") or {}
        if candidate is not None and candidate.is_dir() and isinstance(current_work_item, dict) and current_work_item.get("id"):
            binding = {
                "status": "unique", "task_dir": str(candidate),
                "work_item": {
                    "id": str(current_work_item.get("id")),
                    "provider": str(current_work_item.get("provider") or "noop"),
                },
            }
    if binding["status"] != "unique":
        return {"decision": "block", "reason": "CI_TASK_BINDING_INVALID"}
    credential_task_id = str(
        credential.get("ci_task_id") or credential.get("task_id") or ""
    ).strip()
    if (require_ci_task_id and credential_task_id != task_id) or (
        credential_task_id and credential_task_id != task_id
    ):
        return {"decision": "block", "reason": "CI_PLANNING_TASK_ID_MISMATCH"}

    expected_dir = Path(str(binding["task_dir"])).resolve()
    raw_task_dir = str(credential.get("task_dir") or "").strip()
    current_dir = Path(raw_task_dir) if raw_task_dir else Path()
    if raw_task_dir and not current_dir.is_absolute():
        current_dir = product / current_dir
    try:
        current_dir = current_dir.resolve()
        expected_dir.relative_to(layout.tasks.resolve())
        current_dir.relative_to(layout.tasks.resolve())
    except (OSError, ValueError):
        return {"decision": "block", "reason": "CI_PLANNING_TASK_DIR_MISMATCH"}
    if not current_dir.is_dir() or current_dir != expected_dir:
        return {"decision": "block", "reason": "CI_PLANNING_TASK_DIR_MISMATCH"}

    expected_work_item = binding["work_item"] or {}
    current_work_item = credential.get("work_item") or {}
    expected_work_item_id = work_item_id or str(expected_work_item.get("id") or "")
    expected_provider = provider or str(expected_work_item.get("provider") or "")
    if not isinstance(current_work_item, dict) or (
        str(current_work_item.get("id") or "") != expected_work_item_id
        or str(expected_work_item.get("id") or "") != expected_work_item_id
    ):
        return {"decision": "block", "reason": "CI_PLANNING_WORK_ITEM_MISMATCH"}
    if (
        str(current_work_item.get("provider") or "") != expected_provider
        or str(expected_work_item.get("provider") or "") != expected_provider
    ):
        return {"decision": "block", "reason": "CI_PLANNING_PROVIDER_MISMATCH"}
    if require_ci_task_id:
        if credential.get("schema") != CI_PLANNING_SCHEMA:
            return {"decision": "block", "reason": "CI_PLANNING_SCHEMA_INVALID"}
        bound_changed = credential.get("changed_files")
        if (
            not isinstance(bound_changed, list)
            or not all(isinstance(path, str) for path in bound_changed)
            or len(bound_changed) != len(set(bound_changed))
            or credential.get("changed_files_digest") != canonical_digest(bound_changed)
        ):
            return {"decision": "block", "reason": "CI_CHANGED_FILES_BINDING_INVALID"}
        if changed_files is not None and bound_changed != list(dict.fromkeys(changed_files)):
            return {"decision": "block", "reason": "CI_CHANGED_FILES_MISMATCH"}
        source = next(
            (path for path in (
                expected_dir / "planning_gate_pass.json", expected_dir / "phase0_pass.json",
            ) if path.is_file()),
            None,
        )
        if source is None:
            return {"decision": "block", "reason": "CI_PLANNING_SOURCE_MISSING"}
        try:
            expected_ref = str(source.resolve().relative_to(product.resolve())).replace("\\", "/")
            expected_digest = _sha256(source)
        except (OSError, ValueError):
            return {"decision": "block", "reason": "CI_PLANNING_SOURCE_INVALID"}
        if credential.get("committed_source_ref") != expected_ref:
            return {"decision": "block", "reason": "CI_PLANNING_SOURCE_REF_MISMATCH"}
        if credential.get("committed_source_digest") != expected_digest:
            return {"decision": "block", "reason": "CI_PLANNING_SOURCE_DIGEST_MISMATCH"}
    return {"decision": "pass", "reason": "CI_PLANNING_CREDENTIAL_BOUND"}


def validate_ci_planning_credential(
    harness: Path, product: Path, task_id: str, credential: dict[str, Any],
) -> dict[str, str]:
    return validate_planning_credential(
        harness, product, task_id, credential, require_ci_task_id=True,
    )
