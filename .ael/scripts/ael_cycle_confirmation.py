#!/usr/bin/env python3
"""Explicit story confirmation command."""
from __future__ import annotations

import json
from pathlib import Path

from codex_usage_receipt import automatic_receipt
from ael_cycle_stages import STAGE_ORDER, begin_stage, finish_stage, start_story_cycle
from ael_lifecycle_preflight import discover as discover_lifecycle
from ael_gate_work_items import committed_work_item_resolution
from ael_output import dump_json
from ael_phase_handoff import write as write_phase_handoff
from ael_runtime import (
    active_task_path, atomic_write_result, canonical_digest, default_result, load_result, now,
    policy_for, result_path, task_kind_tier, task_operation_lock,
)
from ael_task_resolution import bind_active_task, valid_task_id
from ael_timing import initialize_cycle
from ael_usage_ledger import capture_usage_baseline
from worktree_baseline import capture_baseline


def _preserve_existing(result: dict, cycle: dict) -> int:
    dump_json({
        "decision": "pass", "reason": "STORY_CONFIRMATION_PRESERVED",
        "confirmed_at": cycle["confirmed_at"], "result": result,
    })
    return 0


def _promote_implicit_confirmation(
    result: dict, path: Path, task_id: str, confirmed_at: str,
) -> int:
    cycle = initialize_cycle(result)
    task = result.get("task") or {}
    if task.get("confirmation") != "implicit-direct-start":
        return _preserve_existing(result, cycle)
    takeover = (cycle.get("stages") or {}).get("takeover") or {}
    later = [
        stage for stage in STAGE_ORDER[1:]
        if ((cycle.get("stages") or {}).get(stage) or {}).get("status")
        not in {None, "pending"}
    ]
    if (cycle.get("current_stage") != "takeover"
            or takeover.get("status") != "active" or later):
        dump_json({
            "decision": "block", "reason": "STORY_CONFIRMATION_TOO_LATE",
            "stage": str(cycle.get("current_stage") or ""),
            "advanced_stages": later,
            "next_action": "start a new explicitly confirmed Story; do not reset the clock",
        })
        return 0
    task["confirmation"] = "explicit"
    result["task"] = task
    result["binding_digest"] = canonical_digest(task)
    cycle["confirmed_at"] = confirmed_at
    cycle["release_readback_enforced"] = True
    outcome = finish_stage(
        result, path, task_id, "takeover", decision="pass",
        reason="CONFIRMATION_AND_SCOPE_CAPTURED", tool_wait_ms="unknown",
    )
    if outcome["decision"] == "pass":
        outcome = begin_stage(result, path, task_id, "planning")
    handoff = None
    if outcome["decision"] == "pass":
        handoff = write_phase_handoff(result, path.parent, "planning")
    atomic_write_result(path, result)
    if outcome["decision"] == "block":
        dump_json(outcome)
        return 0
    dump_json({
        "decision": "pass", "reason": "STORY_CONFIRMATION_UPGRADED",
        "confirmed_at": confirmed_at, "deadline_at": cycle["deadline_at"],
        "lifecycle": result.get("lifecycle") or {}, "handoff": handoff,
    })
    return 0


def cmd_confirm(args) -> int:
    confirmed_at = now()
    product, harness = Path(args.product_root).resolve(), Path(args.ael_root).resolve()
    task_id = str(args.task_id or "").strip()
    if not valid_task_id(task_id):
        dump_json({"decision": "block", "reason": "TASK_ID_INVALID"})
        return 0
    path = result_path(product, task_id)
    active = active_task_path(product)
    with task_operation_lock(active), task_operation_lock(path):
        if path.is_file():
            result = load_result(path)
            existing_work_item = result.get("work_item") or {}
            if not bind_active_task(
                product, task_id, str(existing_work_item.get("id") or ""),
            ):
                dump_json({"decision": "block", "reason": "ACTIVE_TASK_BINDING_CONFLICT"})
                return 0
            cycle = initialize_cycle(result)
            return _promote_implicit_confirmation(result, path, task_id, confirmed_at)
        requested_work_item = str(getattr(args, "work_item", "") or task_id).strip()
        provider_hint = str(getattr(args, "provider", "") or "").strip().lower()
        resolution = committed_work_item_resolution(harness, product, task_id)
        if resolution.get("status") != "unique" and requested_work_item != task_id:
            resolution = committed_work_item_resolution(
                harness, product, requested_work_item,
            )
        if resolution.get("status") == "ambiguous":
            dump_json({"decision": "block", "reason": "WORK_ITEM_BINDING_AMBIGUOUS"})
            return 0
        resolved = resolution.get("work_item") or {}
        resolved_id = str(resolved.get("id") or "").strip()
        resolved_provider = str(resolved.get("provider") or "").strip().lower()
        if resolved_id and requested_work_item and resolved_id != requested_work_item:
            dump_json({"decision": "block", "reason": "WORK_ITEM_BINDING_CONFLICT"})
            return 0
        if provider_hint and resolved_provider and provider_hint != resolved_provider:
            dump_json({"decision": "block", "reason": "WORK_ITEM_PROVIDER_MISMATCH"})
            return 0
        work_item_id = resolved_id or requested_work_item
        tier = task_kind_tier(
            str(getattr(args, "kind", "implementation") or "implementation"),
            str(getattr(args, "tier", "strict") or "strict"),
        )
        requested_provider = resolved_provider or provider_hint
        lifecycle = discover_lifecycle(product, requested_provider)
        provider = requested_provider or str(lifecycle.get("provider") or "").strip().lower()
        if not provider:
            provider = "noop"
        work_item = {"id": work_item_id, "provider": provider} if work_item_id else None
        if tier == "strict" and lifecycle["decision"] == "block":
            dump_json({
                "decision": "block", "reason": lifecycle["reason"],
                "lifecycle": lifecycle,
            })
            return 0
        if not bind_active_task(product, task_id, work_item_id):
            dump_json({"decision": "block", "reason": "ACTIVE_TASK_BINDING_CONFLICT"})
            return 0
        result = default_result(task_id, initial_tier=tier, work_item=work_item)
        scope = sorted({
            str(item).strip().rstrip("/") for item in getattr(args, "scope", [])
            if str(item).strip()
        })
        binding = {
            "task_id": task_id, "scope": scope, "tier_floor": tier,
            "work_item": work_item_id or None,
            "kind": str(getattr(args, "kind", "implementation") or "implementation"),
            "primary_role": "lead-agent", "confirmation": "explicit",
        }
        result["task"] = binding
        result["binding_digest"] = canonical_digest(binding)
        result["policy_digest"] = policy_for(harness, product)
        result["lifecycle"] = lifecycle
        result["invariants"]["task_identity"] = "pass"
        start_story_cycle(
            result, path, task_id, work_item, confirmed_at=confirmed_at,
        )
        finish_stage(
            result, path, task_id, "takeover", decision="pass",
            reason="CONFIRMATION_AND_SCOPE_CAPTURED", tool_wait_ms="unknown",
        )
        begin_stage(result, path, task_id, "planning")
        baseline = path.parent / "worktree_baseline.json"
        capture_baseline(product, baseline, work_item_id=work_item_id)
        result["baseline"] = {
            "digest": canonical_digest(json.loads(baseline.read_text(encoding="utf-8"))),
            "source": "confirmation",
        }
        capture_usage_baseline(result, automatic_receipt(
            task_id, result["subject"]["digest"], result["policy_digest"],
        ))
        handoff = write_phase_handoff(result, path.parent, "planning")
        atomic_write_result(path, result)
    dump_json({
        "decision": "pass", "reason": "STORY_CONFIRMED",
        "confirmed_at": confirmed_at, "deadline_at": result["cycle"]["deadline_at"],
        "lifecycle": lifecycle, "handoff": handoff,
    })
    return 0
