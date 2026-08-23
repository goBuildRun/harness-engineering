#!/usr/bin/env python3
"""Story usage-baseline command."""
from __future__ import annotations

from pathlib import Path

from codex_usage_receipt import automatic_receipt
from harness_output import dump_json
from harness_runtime import (
    atomic_write_result, load_result, policy_for, result_path, subject_for, task_operation_lock,
)
from harness_task_resolution import valid_task_id
from harness_usage_ledger import capture_usage_baseline
from worktree_baseline import changed_since_baseline


def cmd_usage_baseline(args) -> int:
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    if not valid_task_id(args.task_id):
        dump_json({"decision": "block", "reason": "TASK_ID_INVALID"})
        return 0
    path = result_path(product, args.task_id)
    reason = str(args.reason or "").strip()
    if not reason:
        dump_json({"decision": "block", "reason": "USAGE_BASELINE_REASON_REQUIRED"})
        return 0
    with task_operation_lock(path):
        if not path.is_file():
            dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
            return 0
        result = load_result(path)
        changed = changed_since_baseline(product, path.parent / "worktree_baseline.json")
        subject = subject_for(product, changed)
        policy = policy_for(harness, product)
        receipt = automatic_receipt(args.task_id, subject, policy)
        if receipt is None:
            dump_json({"decision": "block", "reason": "USAGE_BASELINE_ENDPOINT_MISSING"})
            return 0
        previous = result.get("cost", {}).get("story_usage_baseline")
        if not previous:
            capture_usage_baseline(result, receipt)
            result["cost"]["story_usage_baseline_reason"] = reason
        epic_id = str(getattr(args, "epic_id", "") or "").strip()
        if epic_id:
            result.setdefault("task", {})["epic_id"] = epic_id
        if not previous:
            result["cost"].pop("story", None)
        atomic_write_result(path, result)
    dump_json({
        "decision": "pass", "reason": "USAGE_BASELINE_CAPTURED",
        "baseline": result["cost"]["story_usage_baseline"],
        "baseline_preserved": bool(previous),
    })
    return 0
