#!/usr/bin/env python3
"""Batch planning facade used by ``ael plan``."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any
from ael_knowledge import sync_planning
from ael_runtime import canonical_digest, now, task_operation_lock
from ael_task_resolution import valid_task_id
from work_item_providers import WorkItemProvider, get_provider, provider_expected_project_id
from work_item_contract import (
    build_work_item_note,
)
from ael_planning_support import (
    _existing_work_item,
    _gate,
    _local_work_item_id,
    _public_work_item,
    _read_json,
    _shared_input_files,
    _shared_readiness,
    _provider_context_digest,
    _task_binding_digest,
    _task_contract,
    _task_id,
    _task_input_files,
    _task_title,
    _write_task_binding,
)
from workspace_paths import resolve_task_dir

SCHEMA = "harness-planning-batch-v1"
DEFAULT_BUDGETS_MS = {
    "global_preflight": 60_000,
    "shared_planning": 180_000,
    "provider_binding": 60_000,
    "context_sync": 60_000,
}

def _plan_batch_locked(
    layout: Phase0Layout,
    *,
    level: str,
    task_dirs: list[str],
    batch_id: str = "",
    provider_mode: str = "offline",
) -> dict[str, Any]:
    level = level.upper().strip()
    if level not in {"L1", "L2", "L3"}:
        return {"decision": "block", "reason": "INVALID_LEVEL"}
    if provider_mode not in {"offline", "configured"}:
        return {"decision": "block", "reason": "INVALID_PROVIDER_MODE"}
    if not task_dirs:
        return {"decision": "block", "reason": "NO_TASK_DIRS"}
    resolved: list[Path] = []
    for raw in task_dirs:
        try:
            task_dir = resolve_task_dir(layout, raw, cwd=Path.cwd())
        except (OSError, ValueError) as exc:
            return {"decision": "block", "reason": f"TASK_DIR_INVALID: {exc}"}
        if not task_dir.is_dir():
            return {"decision": "block", "reason": f"TASK_DIR_MISSING: {task_dir}"}
        try:
            task_dir.relative_to(layout.product_root.resolve())
        except ValueError:
            return {"decision": "block", "reason": "TASK_DIR_OUTSIDE_PRODUCT"}
        resolved.append(task_dir)
    # One global blocker short-circuits the whole batch. Child-specific
    # contract checks happen only after this shared preflight succeeds.
    if level in {"L2", "L3"}:
        specs = sorted(layout.product_specs.glob("*.md")) if layout.product_specs.is_dir() else []
        if not specs and not layout.context_file.is_file():
            return {"decision": "block", "reason": "GLOBAL_PREFLIGHT_NO_PRODUCT_SPEC"}
        required = ["00-任务卡.md", "03-实施方案.md", "04-实施记录.md"]
        if level == "L3":
            required.extend(["01-需求与背景.md", "02-影响分析.md", "05-QA验收.md", "06-交付结论.md"])
        missing = sorted({name for task_dir in resolved for name in required
                          if not (task_dir / name).is_file()})
        if missing:
            return {"decision": "block", "reason": "GLOBAL_PREFLIGHT_TASK_PACKAGE_INCOMPLETE",
                    "missing": missing}
    shared_inputs = _shared_input_files(layout)
    shared_digest = canonical_digest({"level": level, "inputs": shared_inputs})
    task_keys = [str(path.relative_to(layout.product_root)) for path in resolved]
    stable_batch_key = canonical_digest({"level": level, "shared": shared_digest, "tasks": task_keys})[:16]
    batch_id = batch_id.strip() or f"batch-{stable_batch_key}"
    root = layout.agent_workspace / "planning" / batch_id
    children_root = root / "children"
    provider_name = "noop"
    provider = None
    if provider_mode == "configured":
        try:
            provider = get_provider(layout.ael_root)
            provider_name = provider.name
        except Exception as exc:
            return {"decision": "block", "reason": f"PROVIDER_CONFIG_INVALID: {exc}"}
        if provider_name == "noop":
            return {"decision": "block", "reason": "CONFIGURED_PROVIDER_RESOLVED_NOOP"}
    provider_context_digest = _provider_context_digest(
        provider, provider_mode, provider_expected_project_id(provider) if provider else None,
    )
    cached_bundle = _read_json(root / "planning-bundle.json")
    cached_items = cached_bundle.get("children")
    cached_shape_ok = isinstance(cached_items, list) and all(
        isinstance(item, dict) for item in cached_items
    )
    if provider_mode == "offline" and (
        cached_shape_ok
        and
        cached_bundle.get("decision") == "pass"
        and cached_bundle.get("level") == level
        and cached_bundle.get("provider_mode") == provider_mode
        and cached_bundle.get("shared_digest") == shared_digest
        and cached_bundle.get("provider_context_digest") == provider_context_digest
        and [str(item.get("task_dir") or "") for item in cached_items]
            == [str(path) for path in resolved]
    ):
        try:
            context_sync = sync_planning(layout)
        except Exception as exc:
            return {"decision": "block", "reason": f"PLANNING_CONTEXT_SYNC_FAILED: {exc}",
                    "batch_id": batch_id}
        cached_children = []
        cache_valid = True
        for item in cached_items:
            child = _read_json(root / "children" / f"{item.get('work_item_id')}.json")
            child_task_dir = Path(str(item.get("task_dir")))
            current_digest = canonical_digest(_task_input_files(child_task_dir))
            current_binding_digest = _task_binding_digest(child_task_dir)
            if (child.get("schema") != "harness-planning-child-v1"
                    or child.get("decision") != "pass"
                    or str(child.get("work_item_id") or "") != str(item.get("work_item_id") or "")
                    or str(child.get("task_dir") or "") != str(child_task_dir)
                    or child.get("provider_context_digest") != provider_context_digest
                    or not isinstance(child.get("provider_receipt"), dict)
                    or not isinstance(child.get("planning_gate"), dict)
                    or child["planning_gate"].get("decision") != "pass"
                    or child.get("input_digest") != current_digest
                    or child.get("binding_digest") != current_binding_digest):
                cache_valid = False
                break
            cached_children.append({**item, "cache_hit": True})
        if cache_valid and len(cached_children) == len(resolved):
            return {**cached_bundle, "cache_hit": True, "children": cached_children,
                    "context_sync": context_sync}
    children_root.mkdir(parents=True, exist_ok=True)
    shared_readiness = _shared_readiness(layout, level, resolved, shared_inputs)
    inputs = shared_inputs + [
        item for task_dir in resolved for item in _task_input_files(task_dir)
    ]
    input_digest = canonical_digest(inputs)
    started = now()
    started_clock = time.monotonic()
    def budget_guard(stage: str, budget_ms: int) -> dict[str, Any] | None:
        elapsed_ms = int((time.monotonic() - started_clock) * 1000)
        if elapsed_ms > budget_ms:
            return {"decision": "block", "reason": "STAGE_BUDGET_EXCEEDED",
                    "stage": stage, "elapsed_ms": elapsed_ms,
                    "budget_ms": budget_ms, "next_action": "stop_and_escalate"}
        return None
    if shared_readiness["decision"] == "block":
        return {"decision": "block", "reason": "SHARED_READINESS_BLOCKED",
                "batch_id": batch_id, "shared_readiness": shared_readiness}
    children: list[dict[str, Any]] = []
    for index, task_dir in enumerate(resolved, start=1):
        child_inputs = _task_input_files(task_dir)
        wid = _task_id(task_dir, level, index, child_inputs, provider_mode)
        if not wid:
            return {
                "decision": "block", "reason": "WORK_ITEM_ID_REQUIRED",
                "task_dir": str(task_dir), "batch_id": batch_id,
            }
        contract = _task_contract(layout, task_dir, level)
        if contract.get("error") and provider_mode == "configured":
            return {"decision": "block", "reason": str(contract["error"]),
                    "work_item_id": wid, "batch_id": batch_id}
        existing = _existing_work_item(task_dir)
        if existing.get("provider") and existing["provider"] != provider_name:
            return {"decision": "block", "reason": "WORK_ITEM_PROVIDER_MISMATCH",
                    "work_item_id": existing.get("id") or wid,
                    "existing_provider": existing["provider"],
                    "configured_provider": provider_name, "batch_id": batch_id}
        child_path = children_root / f"{wid}.json"
        current_binding_digest = _task_binding_digest(task_dir)
        previous_child = _read_json(child_path)
        if provider_mode == "offline" and (
            previous_child.get("decision") == "pass"
            and previous_child.get("input_digest") == canonical_digest(child_inputs)
            and previous_child.get("shared_readiness_digest") == shared_readiness["shared_digest"]
            and previous_child.get("provider_context_digest") == provider_context_digest
            and previous_child.get("binding_digest") == current_binding_digest
        ):
            children.append({
                "work_item_id": wid, "task_dir": str(task_dir), "decision": "pass",
                "input_digest": previous_child["input_digest"], "cache_hit": True,
            })
            continue
        provider_receipt: dict[str, Any] = {
            "mode": provider_mode, "provider": provider_name,
            "action": "offline-binding" if provider_mode == "offline" else "existing",
            "work_item_id": wid,
            "expected_project_id": provider_expected_project_id(provider) if provider else None,
            "expected_parent_id": contract.get("expected_parent_id"),
            "binding": {
                "decision": "pending", "expected_project_id": provider_expected_project_id(provider) if provider else None,
                "expected_parent_id": contract.get("expected_parent_id"),
                "actual_parent_id": "unknown",
            },
        }
        work_item = {"id": wid, "provider": provider_name}
        if provider is not None and provider_name != "noop":
            try:
                expected_project = provider_expected_project_id(provider)
                expected_parent = contract.get("expected_parent_id")
                if (
                    (expected_project or expected_parent is not None)
                    and type(provider).verify_binding is WorkItemProvider.verify_binding
                ):
                    return {
                        "decision": "block", "reason": "PROVIDER_BINDING_UNSUPPORTED",
                        "work_item_id": wid, "batch_id": batch_id,
                    }
                if not existing.get("id"):
                    title = _task_title(task_dir)
                    spec_path = Path(str(contract.get("spec_path") or ""))
                    note = build_work_item_note(spec_path, title) if spec_path.is_file() else title
                    item = (
                        provider.create_subtask(expected_parent, title=title, note=note)
                        if expected_parent else provider.create(title=title, note=note, project_id=expected_project)
                    )
                    ok, reason = provider.verify_item_binding(
                        item, expected_project_id=expected_project,
                        expected_parent_id=expected_parent,
                    )
                    provider_receipt.update({
                        "action": "create", "create_response": _public_work_item(item),
                        "readback": {
                            "decision": "pass" if ok else "block", "reason": reason,
                            "work_item": _public_work_item(item),
                        },
                    })
                    provider_receipt["binding"].update({
                        "decision": "pass" if ok else "block",
                        "actual_parent_id": str((item.raw or {}).get("parent_task_guid")
                                                 or (item.raw or {}).get("parent_work_item_id") or "unknown"),
                    })
                    if not ok:
                        return {"decision": "block", "reason": "PROVIDER_BINDING_VERIFY_FAILED",
                                "work_item_id": wid, "provider_reason": reason, "batch_id": batch_id,
                                "provider_receipt": provider_receipt}
                    if not valid_task_id(str(item.id)) or any(
                        part in str(item.id) for part in ("/", "\\", "..")
                    ):
                        return {
                            "decision": "block", "reason": "PROVIDER_WORK_ITEM_ID_INVALID",
                            "provider_id": str(item.id), "batch_id": batch_id,
                        }
                    work_item = {"id": item.id, "provider": provider_name}
                    wid = item.id
                    provider_receipt["work_item_id"] = wid
                else:
                    ok, reason = provider.verify_binding(
                        existing["id"], expected_project_id=expected_project,
                        expected_parent_id=expected_parent,
                    )
                    provider_receipt["readback"] = {
                        "decision": "pass" if ok else "block", "reason": reason,
                        "work_item_id": existing["id"], "actual_parent_id": "unknown",
                    }
                    provider_receipt["readback"].update(
                        getattr(provider, "_last_binding_readback", {}) or {}
                    )
                    provider_receipt["binding"].update({
                        key: value for key, value in provider_receipt["readback"].items()
                        if key in {"actual_project_id", "actual_parent_id"}
                    })
                    provider_receipt["binding"].update({
                        "decision": "pass" if ok else "block",
                    })
                    if not ok:
                        return {"decision": "block", "reason": "PROVIDER_BINDING_PREFLIGHT_FAILED",
                                "work_item_id": wid, "provider_reason": reason, "batch_id": batch_id,
                                "provider_receipt": provider_receipt}
            except Exception as exc:
                return {"decision": "block", "reason": "PROVIDER_CREATE_OR_READBACK_FAILED",
                        "work_item_id": wid, "provider_reason": str(exc), "batch_id": batch_id,
                        "provider_receipt": provider_receipt}
        _write_task_binding(
            task_dir, work_item, source="harness-plan-batch", contract=contract,
            product_root=layout.product_root,
        )
        binding_digest = _task_binding_digest(task_dir)
        credential = _gate(task_dir, layout.product_root, level, wid, provider_mode, provider_name)
        credential["batch_id"] = batch_id
        credential["shared_readiness_digest"] = shared_readiness["shared_digest"]
        credential["flow_policy"] = {"lean": True, "source": "harness-plan-batch"}
        child = {
            "schema": "harness-planning-child-v1",
            "decision": "pass",
            "work_item_id": wid,
            "task_dir": str(task_dir),
            "level": level,
            "planning_gate": credential,
            "scope_digest": canonical_digest(child_inputs),
            "input_digest": canonical_digest(child_inputs),
            "provider_mode": provider_mode, "provider": provider_name,
            "provider_receipt": provider_receipt,
            "contract": contract,
            "shared_readiness_digest": shared_readiness["shared_digest"],
            "binding_digest": binding_digest,
            "created_at": now(),
        }
        child["provider_context_digest"] = provider_context_digest
        child_path = children_root / f"{wid}.json"
        child_cache_hit = provider_mode == "offline" and (
            previous_child.get("input_digest") == child["input_digest"]
            and previous_child.get("shared_readiness_digest") == child["shared_readiness_digest"]
            and previous_child.get("provider_receipt") == child.get("provider_receipt")
        )
        if child_cache_hit:
            child = {**previous_child, "cache_hit": True}
        else:
            child["cache_hit"] = False
            child_path.write_text(
                json.dumps(child, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        children.append({"work_item_id": wid, "task_dir": str(task_dir),
                         "decision": "pass", "input_digest": child["input_digest"],
                         "cache_hit": bool(child.get("cache_hit"))})
        if blocked := budget_guard("shared_planning", DEFAULT_BUDGETS_MS["shared_planning"]):
            blocked["batch_id"] = batch_id
            return blocked
    try:
        context_sync = sync_planning(layout)
    except Exception as exc:
        return {"decision": "block", "reason": f"PLANNING_CONTEXT_SYNC_FAILED: {exc}",
                "batch_id": batch_id}
    if blocked := budget_guard("context_sync", DEFAULT_BUDGETS_MS["context_sync"]):
        return blocked
    ledger = root / "planning-ledger.jsonl"
    ledger_entries = [json.dumps({
            "schema": SCHEMA, "batch_id": batch_id, "stage": "planning",
            "event": "batch_started", "started_at": started,
            "input_digest": input_digest, "shared_digest": shared_readiness["shared_digest"],
            "child_count": len(children),
            "budget_ms": sum(DEFAULT_BUDGETS_MS.values()),
        }, ensure_ascii=False, sort_keys=True)]
    ledger_entries.extend(json.dumps({
            "schema": SCHEMA, "batch_id": batch_id, "stage": "child",
            "event": "credential_written", "work_item_id": c["work_item_id"],
            "decision": c["decision"], "at": now(),
        }, ensure_ascii=False, sort_keys=True) for c in children)
    ledger.write_text("\n".join(ledger_entries) + "\n", encoding="utf-8")
    receipt = {
        "schema": SCHEMA, "decision": "pass", "reason": "PLANNING_BATCH_READY",
        "batch_id": batch_id, "level": level, "provider_mode": provider_mode,
        "input_digest": input_digest, "shared_digest": shared_readiness["shared_digest"],
        "provider_context_digest": provider_context_digest,
        "inputs": inputs, "shared_inputs": shared_inputs, "children": children,
        "context_sync": context_sync, "shared_readiness": shared_readiness,
        "cache_hit": bool(shared_readiness.get("cache_hit")) and all(c.get("cache_hit") for c in children),
        "planning_ledger": str(ledger), "started_at": started, "finished_at": now(),
        "budgets_ms": DEFAULT_BUDGETS_MS,
        "next_action": "ael start <work-item-id>",
    }
    (root / "planning-bundle.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "context-sync.json").write_text(
        json.dumps(context_sync, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "provider-readback.json").write_text(
        json.dumps({"schema": "harness-provider-readback-v1", "mode": provider_mode,
                    "provider": provider_name,
                    "decision": "pending" if provider_mode == "offline" else "pass",
                    "children": children,
                    "binding_digest": canonical_digest(children)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt
def plan_batch(
    layout,
    *,
    level: str,
    task_dirs: list[str],
    batch_id: str = "",
    provider_mode: str = "offline",
) -> dict[str, Any]:
    lock = layout.agent_workspace / ".planning.operation"
    with task_operation_lock(lock):
        return _plan_batch_locked(
            layout, level=level, task_dirs=task_dirs, batch_id=batch_id,
            provider_mode=provider_mode,
        )
def main() -> int:
    from ael_planning_cli import main as cli_main
    return cli_main()
if __name__ == "__main__":
    raise SystemExit(main())
