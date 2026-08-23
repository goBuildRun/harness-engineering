#!/usr/bin/env python3
"""Recheck the candidate and bounded deploy stage after a real Provider call."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness_candidate import bound_candidate
from harness_cycle_stage_commands import load_candidate_snapshot, validate_current_release_evidence
from harness_runtime import canonical_digest, load_result
from harness_timing import budget_status, stage_budget_status
from worktree_baseline import changed_since_baseline


def authorization_digest(result: dict[str, Any]) -> str:
    return canonical_digest(result)


def remaining_provider_seconds(
    result: dict[str, Any], final_timeout_seconds: float, *,
    at: str | None = None, epoch_ms: int | None = None,
) -> float:
    cycle = result.get("cycle") or {}
    if final_timeout_seconds <= 0 or cycle.get("current_stage") != "deploy_provider":
        return 0.0
    root = budget_status(cycle, at=at)
    stage = stage_budget_status(cycle, "deploy_provider", epoch_ms=epoch_ms)
    if root["decision"] != "pass" or stage["decision"] != "pass":
        return 0.0
    remaining_ms = min(
        int(root["remaining_ms"]), int(stage["remaining_ms"]),
        max(0, int(final_timeout_seconds * 1000)),
    )
    return max(0.0, remaining_ms / 1000)


def provider_readiness(
    result: dict[str, Any], final_timeout_seconds: float,
) -> tuple[dict[str, Any], float]:
    cycle = result.get("cycle") or {}
    if cycle.get("current_stage") != "deploy_provider":
        return {"decision": "block", "reason": "PROVIDER_STAGE_NOT_ACTIVE"}, 0.0
    state = (cycle.get("stages") or {}).get("deploy_provider") or {}
    attempts = state.get("attempts") or []
    if state.get("status") != "active" or not attempts:
        return {"decision": "block", "reason": "PROVIDER_STAGE_NOT_ACTIVE"}, 0.0
    for status in (budget_status(cycle), stage_budget_status(cycle, "deploy_provider")):
        if status["decision"] == "block":
            return status, 0.0
    remaining = remaining_provider_seconds(result, final_timeout_seconds)
    if remaining <= 0:
        return {"decision": "block", "reason": "PROVIDER_DEADLINE_EXCEEDED"}, 0.0
    return {"decision": "pass", "reason": "PROVIDER_READY"}, remaining


def validate(
    product: Path, task_path: Path, baseline: Path, expected_subject: str,
    *, expected_result_digest: str = "",
) -> dict[str, Any]:
    current_result = load_result(task_path)
    if (
        expected_result_digest
        and authorization_digest(current_result) != expected_result_digest
    ):
        return {
            "decision": "block",
            "reason": "PROVIDER_RESULT_CHANGED_DURING_ATTEMPT",
        }
    snapshot = load_candidate_snapshot(task_path)
    changed = changed_since_baseline(product, baseline)
    current = (
        bound_candidate(product, snapshot, current_result.get("candidate"), changed)
        if snapshot else {"decision": "block", "reason": "CANDIDATE_NOT_FROZEN"}
    )
    if (
        current["decision"] == "block"
        or str(current.get("candidate_digest") or "") != expected_subject
    ):
        return {
            "decision": "block",
            "reason": "PROVIDER_CANDIDATE_CHANGED_DURING_ATTEMPT",
        }
    cycle = current_result.get("cycle") or {}
    stage = (cycle.get("stages") or {}).get("deploy_provider") or {}
    if (
        cycle.get("current_stage") != "deploy_provider"
        or stage.get("status") != "active"
        or not stage.get("attempts")
    ):
        return {"decision": "block", "reason": "PROVIDER_STAGE_NOT_ACTIVE"}
    for status in (budget_status(cycle), stage_budget_status(cycle, "deploy_provider")):
        if status["decision"] == "block":
            return status
    return {"decision": "pass", "reason": "PROVIDER_POSTCONDITION_OK"}


def validate_task_precondition(
    product: Path, task_path: Path, baseline: Path, task_id: str, expected_subject: str,
    expected_result_digest: str,
) -> dict[str, Any]:
    status = validate(
        product, task_path, baseline, expected_subject,
        expected_result_digest=expected_result_digest,
    )
    if status["decision"] == "block":
        return status
    return validate_current_release_evidence(product, task_id, load_result(task_path))
