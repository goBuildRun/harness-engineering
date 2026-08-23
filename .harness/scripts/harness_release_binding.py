#!/usr/bin/env python3
"""Immutable lifecycle and attested-result bindings for release authorization."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness_runtime import canonical_digest
from harness_timing import STAGE_BUDGETS_MS, ledger_path, parse_time, read_events


def release_binding(result: dict[str, Any]) -> dict[str, Any]:
    cycle = result.get("cycle") or {}
    return {
        "task_id": result.get("task_id"),
        "work_item": result.get("work_item"),
        "task": result.get("task"),
        "binding_digest": result.get("binding_digest"),
        "policy_digest": result.get("policy_digest"),
        "tier": result.get("tier"),
        "lifecycle": result.get("lifecycle"),
        "candidate": result.get("candidate"),
        "subject": result.get("subject"),
        "evidence_manifest": result.get("evidence_manifest"),
        "checks": result.get("checks"),
        "canonical_finish": cycle.get("canonical_finish"),
    }


def attestation_result_status(
    result: dict[str, Any], attestation: dict[str, Any],
) -> dict[str, str]:
    attested = attestation.get("result")
    valid = (
        isinstance(attested, dict)
        and attested.get("decision") == "pass"
        and attested.get("state") == "validated"
        and canonical_digest(release_binding(attested))
        == canonical_digest(release_binding(result))
    )
    return {
        "decision": "pass" if valid else "block",
        "reason": (
            "ATTESTATION_RESULT_BOUND" if valid
            else "ATTESTATION_RESULT_BINDING_MISMATCH"
        ),
    }


def configured_provider(result: dict[str, Any]) -> str:
    work_item = result.get("work_item") or {}
    lifecycle = result.get("lifecycle") or {}
    return str(
        (work_item.get("provider") if isinstance(work_item, dict) else "")
        or (lifecycle.get("provider") if isinstance(lifecycle, dict) else "")
        or "noop"
    ).strip().lower()


def provider_binding_status(result: dict[str, Any]) -> dict[str, str]:
    work_item = result.get("work_item") or {}
    lifecycle = result.get("lifecycle") or {}
    work_provider = str(
        (work_item.get("provider") if isinstance(work_item, dict) else "") or ""
    ).strip().lower()
    lifecycle_provider = str(
        (lifecycle.get("provider") if isinstance(lifecycle, dict) else "") or ""
    ).strip().lower()
    if work_provider and lifecycle_provider and work_provider != lifecycle_provider:
        return {"decision": "block", "reason": "LIFECYCLE_PROVIDER_MISMATCH"}
    return {"decision": "pass", "reason": "LIFECYCLE_PROVIDER_BOUND"}


def closed_cycle_status(
    result: dict[str, Any], path: Path, task_id: str, work_item_id: str,
) -> dict[str, str]:
    cycle = result.get("cycle")
    if not isinstance(cycle, dict) or not cycle.get("stage_enforced"):
        return {"decision": "block", "reason": "STORY_CYCLE_CLOSURE_INVALID"}
    if cycle.get("current_stage") or any(
        ((cycle.get("stages") or {}).get(stage) or {}).get("status") != "pass"
        for stage in STAGE_BUDGETS_MS
    ):
        return {"decision": "block", "reason": "STORY_CYCLE_CLOSURE_INVALID"}
    ended_at = str(cycle.get("ended_at") or "")
    root_wall_ms = cycle.get("root_wall_ms")
    if not ended_at or not isinstance(root_wall_ms, int) or root_wall_ms < 0:
        return {"decision": "block", "reason": "STORY_CYCLE_CLOSURE_INVALID"}
    try:
        events = read_events(ledger_path(path))
        starts = [
            event for event in events
            if event.get("event") == "start" and event.get("span_id") == "story"
        ]
        ends = [
            event for event in events
            if event.get("event") == "end" and event.get("span_id") == "story"
        ]
        if len(starts) != 1 or len(ends) != 1:
            raise ValueError
        start, end = starts[0], ends[0]
        end_wall_ms = end.get("wall_ms")
        if not isinstance(end_wall_ms, int) or end_wall_ms < 0:
            raise ValueError
        started_epoch_ms = int(parse_time(str(cycle.get("started_at") or "")).timestamp() * 1000)
        valid = (
            start.get("task_id") == task_id
            and end.get("task_id") == task_id
            and str(start.get("work_item_id") or "") == work_item_id
            and str(end.get("work_item_id") or "") == work_item_id
            and start.get("stage") == "story"
            and end.get("stage") == "story"
            and start.get("attempt") == 1
            and end.get("attempt") == 1
            and int(start.get("epoch_ms") or -1) == started_epoch_ms
            and int(end.get("epoch_ms") or -1) - end_wall_ms == started_epoch_ms
            and end.get("decision") == "pass"
            and end.get("reason") == "STORY_READY_TO_RELEASE"
            and end.get("timestamp") == ended_at
            and end_wall_ms == root_wall_ms
        )
    except (OSError, TypeError, ValueError):
        return {"decision": "block", "reason": "STORY_LEDGER_INVALID"}
    return {
        "decision": "pass" if valid else "block",
        "reason": "STORY_CYCLE_CLOSED" if valid else "STORY_CYCLE_CLOSURE_INVALID",
    }
