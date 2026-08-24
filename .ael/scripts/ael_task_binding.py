#!/usr/bin/env python3
"""Monotonic strengthening for resumed AEL task bindings."""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from ael_runtime import TIERS, canonical_digest, now, policy_for, task_kind_tier


def resolve_start_work_item(
    planning: dict[str, Any],
    requested_work_item: str,
) -> dict[str, Any]:
    requested = str(requested_work_item or "").strip()
    if planning["status"] == "ambiguous":
        return {
            "decision": "block",
            "reason": "TASK_START_WORK_ITEM_AMBIGUOUS",
            "candidates": planning["candidates"],
        }
    planned_work_item = planning["work_item"]
    if requested and planned_work_item and requested != planned_work_item["id"]:
        return {
            "decision": "block",
            "reason": "TASK_START_WORK_ITEM_MISMATCH",
            "requested_work_item": requested,
            "committed_work_item": planned_work_item["id"],
        }
    work_item = planned_work_item or ({"id": requested} if requested else None)
    return {"decision": "pass", "work_item": work_item}


def strengthen_resumed_task(
    result: dict[str, Any],
    args: Namespace,
    task_id: str,
    harness: Path,
    product: Path,
    *,
    resolved_work_item: dict[str, str] | None = None,
) -> dict[str, Any]:
    current = result.get("work_item") or {}
    current_work_item = str(current.get("id") or "").strip()
    resolved_work_item = resolved_work_item or {}
    requested_work_item = str(resolved_work_item.get("id") or "").strip()
    if requested_work_item and current_work_item and requested_work_item != current_work_item:
        return {
            "decision": "block",
            "reason": "TASK_RESUME_WORK_ITEM_MISMATCH",
            "requested_work_item": requested_work_item,
            "current_work_item": current_work_item,
            "changed": False,
        }
    current_provider = str(current.get("provider") or "").strip()
    requested_provider = str(resolved_work_item.get("provider") or "").strip()
    if current_provider and requested_provider and current_provider != requested_provider:
        return {
            "decision": "block", "reason": "TASK_RESUME_WORK_ITEM_PROVIDER_MISMATCH",
            "requested_provider": requested_provider, "current_provider": current_provider,
            "changed": False,
        }
    target_work_item = None
    if requested_work_item or current_work_item:
        target_work_item = {"id": requested_work_item or current_work_item}
        provider = requested_provider or current_provider
        if provider:
            target_work_item["provider"] = provider

    requested_tier = task_kind_tier(
        str(getattr(args, "kind", "implementation") or "implementation"),
        str(getattr(args, "tier", "standard") or "standard"),
    )
    current_tier = str((result.get("tier") or {}).get("initial") or "standard")
    target_tier = max((current_tier, requested_tier), key=TIERS.__getitem__)
    previous_task = dict(result.get("task") or {})
    current_scope = {
        str(item).strip().rstrip("/")
        for item in previous_task.get("scope") or []
        if str(item).strip()
    }
    requested_scope = {
        str(item).strip().rstrip("/")
        for item in getattr(args, "scope", [])
        if str(item).strip()
    }
    merged_scope = sorted(current_scope | requested_scope)
    strengthened = target_tier != current_tier or merged_scope != sorted(current_scope)
    if target_work_item != (current or None):
        strengthened = True
        result["work_item"] = target_work_item

    if not strengthened:
        return {"decision": "pass", "reason": "TASK_RESUMED", "result": result, "changed": False}

    previous_binding_digest = str(result.get("binding_digest") or "")
    binding = {
        **previous_task,
        "task_id": task_id,
        "scope": merged_scope,
        "tier_floor": target_tier,
        "work_item": (target_work_item or {}).get("id") or previous_task.get("work_item"),
        "kind": previous_task.get("kind") or getattr(args, "kind", "implementation"),
    }
    result["task"] = binding
    result["tier"]["initial"] = target_tier
    effective = str(result["tier"].get("effective") or current_tier)
    result["tier"]["effective"] = max((effective, target_tier), key=TIERS.__getitem__)
    result.setdefault("binding_revisions", []).append(
        {
            "amended_at": now(),
            "reason": "monotonic resume strengthening",
            "previous_binding_digest": previous_binding_digest,
            "previous_scope": sorted(current_scope),
            "scope": merged_scope,
            "previous_tier": current_tier,
            "tier": target_tier,
        }
    )
    result["binding_digest"] = canonical_digest(binding)
    result["policy_digest"] = policy_for(harness, product)
    result["state"] = "active"
    result["decision"] = "block"
    result["blockers"] = sorted(set(result.get("blockers") or []) | {"TASK_BINDING_CHANGED"})
    result["invariants"]["scope"] = "pending"
    result["invariants"]["risk_validation"] = "pending"
    result["invariants"]["final_result"] = "pending"
    result.get("cost", {}).pop("receipt", None)
    for check in result.get("checks", {}).values():
        check["stale"] = True
    return {
        "decision": "pass",
        "reason": "TASK_RESUMED_WITH_STRONGER_BINDING",
        "result": result,
        "changed": True,
    }
