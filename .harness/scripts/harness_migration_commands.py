#!/usr/bin/env python3
"""CLI handlers for read-only workspace audit and explicit task migration."""
from __future__ import annotations

import json
from pathlib import Path

from harness_gates import committed_work_item
from harness_migration import audit_workspace, migration_eligibility, repair_migrated_identity
from harness_output import dump_json
from harness_runtime import atomic_write_result, canonical_digest, default_result, policy_for, result_path
from worktree_baseline import capture_baseline


def cmd_audit(args) -> int:
    product = Path(args.product_root).resolve()
    dump_json({"decision": "pass", "reason": "WORKSPACE_AUDIT", "audit": audit_workspace(product)})
    return 0


def cmd_migrate(args) -> int:
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    path = result_path(product, args.task_id)
    work_item = committed_work_item(harness, product, args.task_id)
    if path.exists():
        result, repaired = repair_migrated_identity(path, args.task_id, work_item)
        reason = "TASK_MIGRATION_IDENTITY_REPAIRED" if repaired else "TASK_ALREADY_MIGRATED"
        dump_json({"decision": "pass", "reason": reason, "result": result})
        return 0
    allowed, reason = migration_eligibility(product, args.task_id)
    if not allowed:
        dump_json({"decision": "block", "reason": reason, "task_id": args.task_id})
        return 0
    result = default_result(args.task_id, initial_tier="standard", work_item=work_item)
    result["baseline"]["source"] = "migration"
    result["policy_digest"] = policy_for(harness, product)
    binding = {"task_id": args.task_id, "work_item": work_item,
               "tier_floor": "standard", "source": "migration"}
    result["binding_digest"] = canonical_digest(binding)
    if work_item:
        result["invariants"]["task_identity"] = "pass"
    baseline = path.parent / "worktree_baseline.json"
    capture_baseline(product, baseline, work_item_id=args.task_id,
                     mode="recovery", reason=args.reason)
    result["baseline"]["digest"] = canonical_digest(json.loads(baseline.read_text()))
    result["blockers"] = ["STANDARD_REVALIDATION_REQUIRED"]
    atomic_write_result(path, result)
    dump_json({"decision": "pass", "reason": "TASK_MIGRATED_REVALIDATION_REQUIRED", "result": result})
    return 0
