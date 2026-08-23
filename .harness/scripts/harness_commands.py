#!/usr/bin/env python3
"""Command handlers for the lean Harness runtime."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from harness_candidate import bound_candidate, refresh_evidence, release_evidence_paths
from harness_output import dump_json
from harness_assurance import finalize
from harness_attestation import verify_attestation
from harness_cache import executed_check, reuse_check, tool_digest
from harness_gates import checks_for_tier, committed_work_item_resolution, run_gate_plan
from harness_telemetry import apply_automatic_usage, enforce_budget
from codex_usage_receipt import automatic_receipt
from harness_usage_ledger import aggregate_epic_usage, apply_story_usage, capture_usage_baseline
from harness_runtime import (
    active_task_path, apply_code_health, atomic_write_result, canonical_digest, classify_tier,
    default_result, task_kind_tier, fingerprint, finish_decision, git_changed, invoke_gc_once,
    load_result, mechanical_code_health, now, policy_for, resolve_task_id, result_path,
    subject_for, task_operation_lock, workspace_root,
)
from harness_scope import execution_paths, paths_within_scope
from harness_state import invalidate_if_stale
from worktree_baseline import capture_baseline, changed_since_baseline
from harness_ci import cmd_ci_check
from harness_task_binding import resolve_start_work_item, strengthen_resumed_task
from harness_task_resolution import bind_active_task, valid_task_id
from harness_timing import budget_status, ledger_path, stage_budget_status
from harness_cycle_commands import (
    allow_finish_attempt, begin_finish_span, cmd_stage, cmd_usage_baseline,
    complete_finish_span, load_candidate_snapshot, refresh_assurance, start_story_cycle,
)
from harness_lifecycle_preflight import discover as discover_lifecycle, preflight_resumed
import harness_finish


def cmd_start(args: argparse.Namespace) -> int:
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    task_id = args.task_id or f"task-{uuid.uuid4().hex[:12]}"
    if not valid_task_id(task_id):
        dump_json({"decision": "block", "reason": "TASK_ID_INVALID"})
        return 0
    path = result_path(product, task_id)
    selection = resolve_start_work_item(
        committed_work_item_resolution(harness, product, task_id), args.work_item)
    if selection["decision"] == "block":
        dump_json(selection)
        return 0
    shared_lock = active_task_path(product)
    if os.environ.get("HARNESS_TASK_SCOPED") == "1":
        shared_lock = path
    if shared_lock == path:
        with task_operation_lock(path):
            return _cmd_start_locked(args, product, harness, task_id, path, selection)
    with task_operation_lock(shared_lock), task_operation_lock(path):
        return _cmd_start_locked(args, product, harness, task_id, path, selection)


def _cmd_start_locked(
    args: argparse.Namespace, product: Path, harness: Path, task_id: str, path: Path,
    selection: dict[str, object],
) -> int:
    if path.exists():
        outcome = strengthen_resumed_task(load_result(path), args, task_id, harness, product,
                                          resolved_work_item=selection["work_item"])
        if outcome.get("decision") == "block":
            dump_json(outcome)
            return 0
        resumed_work_item = (outcome.get("result") or {}).get("work_item") or {}
        if not bind_active_task(
            product, task_id, str(resumed_work_item.get("id") or ""),
        ):
            dump_json({"decision": "block", "reason": "ACTIVE_TASK_BINDING_CONFLICT"})
            return 0
        outcome = preflight_resumed(outcome, product, selection["work_item"])
        if outcome.pop("changed", False):
            atomic_write_result(path, outcome["result"])
        dump_json(outcome)
        return 0
    work_item = selection["work_item"]
    work_item_id = str((work_item or {}).get("id") or "")
    change_reason = str(getattr(args, "reason", "") or "").strip()
    if args.kind in {"scope-change", "hotfix"} and not change_reason:
        dump_json({"decision": "block", "reason": "TASK_KIND_REASON_REQUIRED", "kind": args.kind})
        return 0
    initial = task_kind_tier(args.kind, args.tier or "standard")
    lifecycle = discover_lifecycle(
        product, str((work_item or {}).get("provider") or ""),
    )
    if initial == "strict" and lifecycle["decision"] == "block":
        dump_json({"decision": "block", "reason": lifecycle["reason"], "lifecycle": lifecycle})
        return 0
    if not bind_active_task(product, task_id, work_item_id):
        dump_json({"decision": "block", "reason": "ACTIVE_TASK_BINDING_CONFLICT"})
        return 0
    result = default_result(task_id, initial_tier=initial, work_item=work_item)
    result["task_scoped_state"] = os.environ.get("HARNESS_TASK_SCOPED") == "1"
    result["lifecycle"] = lifecycle
    baseline = path.parent / "worktree_baseline.json"
    capture_baseline(product, baseline, work_item_id=task_id)
    result["baseline"] = {"digest": canonical_digest(json.loads(baseline.read_text())), "source": "start"}
    result["policy_digest"] = policy_for(harness, product)
    binding = {
        "task_id": task_id, "scope": args.scope, "tier_floor": initial,
        "work_item": (work_item or {}).get("id"), "kind": args.kind, "change_reason": change_reason or None,
        "primary_role": "gc-sweeper" if args.kind == "debt-maintenance" else "lead-agent",
        "confirmation": "implicit-direct-start",
    }
    result["binding_digest"] = canonical_digest(binding)
    result["invariants"]["task_identity"] = "pass"
    result["task"] = binding
    capture_usage_baseline(result, automatic_receipt(
        task_id, result["subject"]["digest"], result["policy_digest"]))
    if initial == "lite":
        binding_path = workspace_root(product) / "planning" / "tasks" / task_id / "task.json"
        binding_path.parent.mkdir(parents=True, exist_ok=True)
        binding_path.write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    start_story_cycle(result, path, task_id, work_item)
    atomic_write_result(path, result)
    dump_json({"decision": "pass", "reason": "TASK_STARTED", "result": result})
    return 0
def cmd_amend(args: argparse.Namespace) -> int:
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    if not valid_task_id(args.task_id):
        dump_json({"decision": "block", "reason": "TASK_ID_INVALID"})
        return 0
    path = result_path(product, args.task_id)
    reason = str(args.reason or "").strip()
    scope = sorted({str(item).strip().rstrip("/") for item in args.scope if str(item).strip()})
    if not reason:
        dump_json({"decision": "block", "reason": "TASK_AMEND_REASON_REQUIRED"})
        return 0
    if not scope or any(Path(item).is_absolute() or ".." in Path(item).parts for item in scope):
        dump_json({"decision": "block", "reason": "TASK_AMEND_SCOPE_INVALID"})
        return 0

    with task_operation_lock(path):
        if not path.is_file():
            dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
            return 0
        result = load_result(path)
        previous = dict(result.get("task") or {})
        binding = {
            **previous,
            "task_id": args.task_id,
            "scope": scope,
            "tier_floor": result["tier"]["initial"],
            "work_item": (result.get("work_item") or {}).get("id"),
            "source": previous.get("source") or result.get("baseline", {}).get("source") or "amendment",
        }
        if getattr(args, "kind", ""):
            binding["kind"] = args.kind
        revisions = list(result.get("binding_revisions") or [])
        revisions.append({
            "amended_at": now(), "reason": reason,
            "previous_binding_digest": result.get("binding_digest") or "",
            "previous_scope": list(previous.get("scope") or []), "scope": scope,
            "previous_kind": previous.get("kind") or "", "kind": binding.get("kind") or "",
        })
        result["task"] = binding
        result["binding_revisions"] = revisions
        result["binding_digest"] = canonical_digest(binding)
        result["policy_digest"] = policy_for(harness, product)
        result.get("cost", {}).pop("receipt", None)
        result["tier"]["effective"] = result["tier"]["initial"]
        result["state"] = "active"
        result["decision"] = "block"
        result["blockers"] = sorted(set(result.get("blockers") or []) | {"TASK_BINDING_CHANGED"})
        result["invariants"]["scope"] = "pending"
        result["invariants"]["risk_validation"] = "pending"
        result["invariants"]["final_result"] = "pending"
        for check in result.get("checks", {}).values():
            check["stale"] = True
        atomic_write_result(path, result)
    dump_json({"decision": "pass", "reason": "TASK_BINDING_AMENDED", "result": result})
    return 0

def cmd_status(args: argparse.Namespace) -> int:
    product = Path(args.product_root).resolve()
    task_id, candidates = resolve_task_id(product, args.task_id)
    if not task_id:
        reason = "TASK_ID_INVALID" if candidates == ["TASK_ID_INVALID"] else "TASK_INFERENCE_AMBIGUOUS"
        dump_json({"decision": "block", "reason": reason, "candidates": candidates})
        return 0
    path = result_path(product, task_id)
    if not path.is_file():
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    stored_result = load_result(path)
    baseline = path.parent / "worktree_baseline.json"
    changed = changed_since_baseline(product, baseline) if baseline.is_file() else git_changed(product)
    current_subject = subject_for(product, changed)
    current_policy = policy_for(Path(args.harness_root).resolve(), product)
    attestation = verify_attestation(
        product, commit="HEAD", policy_digest=current_policy, task_id=task_id,
    )
    committed_result = attestation.get("result") or {}
    committed_current = (
        attestation.get("decision") == "pass"
        and committed_result.get("task_id") == task_id
        and not git_changed(product)
    )
    result = deepcopy(committed_result if committed_current else stored_result)
    if not committed_current:
        invalidate_if_stale(result, current_subject, current_policy)
    refresh_assurance(result, product, current_policy, phase="status-head")
    dump_json({"decision": "pass", "reason": "TASK_STATUS", "result": result})
    return 0

def cmd_finish(args: argparse.Namespace) -> int:
    return harness_finish.execute_finish(args, commands=sys.modules[__name__])
