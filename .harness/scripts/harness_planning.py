#!/usr/bin/env python3
"""Batch planning facade used by ``harness plan``.

The batch receipt is deliberately local and immutable: it records the inputs,
the per-child planning credential, and the provider mode without requiring a
provider call when the external container is not explicitly authorized.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from harness_output import dump_json
from harness_knowledge import sync_planning
from harness_runtime import canonical_digest, now
from harness_task_resolution import valid_task_id
from work_item_providers import extract_from_task_dir, get_provider, provider_expected_project_id
from workspace_paths import Phase0Layout, load_layout, resolve_task_dir

SCHEMA = "harness-planning-batch-v1"
DEFAULT_BUDGETS_MS = {
    "global_preflight": 60_000,
    "shared_planning": 180_000,
    "provider_binding": 60_000,
    "context_sync": 60_000,
}


def _digest_file(path: Path) -> dict[str, str]:
    try:
        data = path.read_bytes()
    except OSError:
        return {"path": str(path), "sha256": "missing"}
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def _input_files(layout: Phase0Layout, task_dirs: list[Path]) -> list[dict[str, str]]:
    roots = [layout.product_specs, layout.exec_plans_active, layout.exec_plans_completed]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(sorted(p for p in root.rglob("*") if p.is_file()))
    for task_dir in task_dirs:
        if task_dir.is_dir():
            files.extend(sorted(p for p in task_dir.rglob("*") if p.is_file()))
    unique = sorted({p.resolve() for p in files})
    return [_digest_file(path) for path in unique]


def _task_id(task_dir: Path, level: str, index: int) -> str:
    work_item, _ = extract_from_task_dir(task_dir)
    if work_item and valid_task_id(work_item):
        return work_item
    if level == "L1":
        return f"local-plan-{index:03d}"
    return ""


def _gate(task_dir: Path, product: Path, level: str, work_item_id: str,
          provider_mode: str, provider_name: str) -> dict[str, Any]:
    return {
        "schema": "harness-planning-credential-v1",
        "decision": "pass",
        "gate": "planning",
        "level": level,
        "task_dir": str(task_dir),
        "product_root": str(product),
        "work_item": {"id": work_item_id, "provider": provider_name},
        "created_at": now(),
        "source": "harness-plan-batch",
    }


def plan_batch(
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
    batch_id = batch_id.strip() or f"batch-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    root = layout.agent_workspace / "planning" / batch_id
    children_root = root / "children"
    children_root.mkdir(parents=True, exist_ok=True)
    inputs = _input_files(layout, resolved)
    input_digest = canonical_digest(inputs)
    provider_name = "noop"
    provider = None
    if provider_mode == "configured":
        try:
            provider = get_provider(layout.harness_root)
            provider_name = provider.name
        except Exception as exc:
            return {"decision": "block", "reason": f"PROVIDER_CONFIG_INVALID: {exc}"}
    started = now()
    started_clock = time.monotonic()
    def budget_guard(stage: str, budget_ms: int) -> dict[str, Any] | None:
        elapsed_ms = int((time.monotonic() - started_clock) * 1000)
        if elapsed_ms > budget_ms:
            return {"decision": "block", "reason": "STAGE_BUDGET_EXCEEDED",
                    "stage": stage, "elapsed_ms": elapsed_ms,
                    "budget_ms": budget_ms, "next_action": "stop_and_escalate"}
        return None
    try:
        context_sync = sync_planning(layout)
    except Exception as exc:
        return {"decision": "block", "reason": f"PLANNING_CONTEXT_SYNC_FAILED: {exc}",
                "batch_id": batch_id}
    if blocked := budget_guard("context_sync", DEFAULT_BUDGETS_MS["context_sync"]):
        return blocked
    children: list[dict[str, Any]] = []
    for index, task_dir in enumerate(resolved, start=1):
        wid = _task_id(task_dir, level, index)
        if not wid:
            return {
                "decision": "block", "reason": "WORK_ITEM_ID_REQUIRED",
                "task_dir": str(task_dir), "batch_id": batch_id,
            }
        if provider is not None and provider_name != "noop":
            try:
                ok, reason = provider.verify_binding(
                    wid, expected_project_id=provider_expected_project_id(provider),
                )
            except Exception as exc:
                ok, reason = False, str(exc)
            if not ok:
                return {"decision": "block", "reason": "PROVIDER_BINDING_PREFLIGHT_FAILED",
                        "work_item_id": wid, "provider_reason": reason, "batch_id": batch_id}
        credential = _gate(task_dir, layout.product_root, level, wid, provider_mode, provider_name)
        child = {
            "schema": "harness-planning-child-v1",
            "decision": "pass",
            "work_item_id": wid,
            "task_dir": str(task_dir),
            "level": level,
            "planning_gate": credential,
            "scope_digest": canonical_digest([_digest_file(p) for p in sorted(task_dir.rglob("*")) if p.is_file()]),
            "provider_mode": provider_mode, "provider": provider_name,
            "created_at": now(),
        }
        (children_root / f"{wid}.json").write_text(
            json.dumps(child, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        children.append({"work_item_id": wid, "task_dir": str(task_dir), "decision": "pass"})
        if blocked := budget_guard("shared_planning", DEFAULT_BUDGETS_MS["shared_planning"]):
            blocked["batch_id"] = batch_id
            return blocked
    ledger = root / "planning-ledger.jsonl"
    ledger_entries = [json.dumps({
            "schema": SCHEMA, "batch_id": batch_id, "stage": "planning",
            "event": "batch_started", "started_at": started,
            "input_digest": input_digest, "child_count": len(children),
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
        "input_digest": input_digest, "inputs": inputs, "children": children,
        "context_sync": context_sync,
        "planning_ledger": str(ledger), "started_at": started, "finished_at": now(),
        "budgets_ms": DEFAULT_BUDGETS_MS,
        "next_action": "harness start <work-item-id>",
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
                    "children": children}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default=os.environ.get("HARNESS_PRODUCT_ROOT", os.getcwd()))
    parser.add_argument("--level", choices=("L1", "L2", "L3"), required=True)
    parser.add_argument("--task-dir", action="append", required=True)
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--provider-mode", choices=("offline", "configured"), default="offline")
    args = parser.parse_args()
    layout = load_layout(Path(args.harness_root).resolve(), Path(args.product_root).resolve())
    result = plan_batch(layout, level=args.level, task_dirs=args.task_dir,
                        batch_id=args.batch_id, provider_mode=args.provider_mode)
    dump_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
