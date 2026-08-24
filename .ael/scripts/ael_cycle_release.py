#!/usr/bin/env python3
"""Canonical finish spans and post-commit release readiness."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ael_candidate import committed_candidate
from ael_attestation import verify_attestation
from ael_cycle_stage_commands import (
    load_candidate_snapshot, validate_current_release_evidence,
)
from ael_cycle_stages import close_story_cycle, finish_readiness, finish_stage
from ael_output import dump_json
from ael_release_readback import build_local_receipt, validate as validate_readback
from ael_release_binding import (
    attestation_result_status as _attestation_result_status,
    closed_cycle_status as _closed_cycle_status,
    configured_provider as _configured_provider,
    provider_binding_status as _provider_binding_status,
)
from ael_runtime import (
    atomic_write_result, canonical_digest, load_result, now, result_path, task_operation_lock,
)
from ael_task_resolution import valid_task_id
from ael_timing import (
    budget_status, end_span, ledger_path, read_events, register_finish_attempt,
    stage_budget_status, start_span,
)


RETRYABLE_RELEASE_READY_REASONS = frozenset({
    "ATTESTATION_COMMIT_INVALID",
    "ATTESTATION_COMMIT_MISMATCH",
    "ATTESTATION_DECISION_BLOCK",
    "ATTESTATION_MISSING",
    "ATTESTATION_POLICY_MISMATCH",
    "ATTESTATION_RESULT_INVALID",
    "ATTESTATION_RESULT_BINDING_MISMATCH",
    "ATTESTATION_RESULT_REF_INVALID",
    "ATTESTATION_SCHEMA_INVALID",
    "ATTESTATION_TASK_MISMATCH",
    "ATTESTATION_TREE_MISMATCH",
    "CANDIDATE_BASELINE_COMMIT_INVALID",
    "CANDIDATE_COMMIT_BINDING_INVALID",
    "CANDIDATE_COMMIT_EVIDENCE_MISMATCH",
    "CANDIDATE_COMMIT_NOT_FOUND",
    "CANDIDATE_COMMIT_MISMATCH",
    "CANDIDATE_COMMIT_PATHS_MISMATCH",
    "CANDIDATE_NOT_FROZEN",
    "CANDIDATE_RESULT_BINDING_MISMATCH",
    "CANDIDATE_SNAPSHOT_DIGEST_MISMATCH",
    "CANDIDATE_SNAPSHOT_INVALID",
    "LIFECYCLE_READBACK_INVALID",
    "LIFECYCLE_TRUSTED_READBACK_REQUIRED",
    "RELEASE_EVIDENCE_MANIFEST_STALE",
    "STORY_CYCLE_CLOSURE_INVALID",
    "STORY_LEDGER_INVALID",
})


def _guarded_release_integrity(
    product: Path, path: Path, task_id: str, result: dict[str, Any], commit: str,
) -> dict[str, Any]:
    provider_binding = _provider_binding_status(result)
    if provider_binding["decision"] == "block":
        return provider_binding
    snapshot = load_candidate_snapshot(path)
    commit_binding = (
        committed_candidate(
            product, snapshot, result.get("candidate"), commit,
            evidence_manifest=None,
        )
        if snapshot else {"decision": "block", "reason": "CANDIDATE_NOT_FROZEN"}
    )
    if commit_binding["decision"] == "block":
        return commit_binding
    resolved_commit = str(commit_binding["commit"])
    attestation = verify_attestation(
        product, commit=resolved_commit, task_id=task_id,
        policy_digest=str(result.get("policy_digest") or ""),
    )
    if attestation["decision"] == "block":
        return attestation
    binding = _attestation_result_status(result, attestation)
    if binding["decision"] == "block":
        return binding
    attested_result = attestation.get("result") or {}
    commit_binding = committed_candidate(
        product, snapshot, result.get("candidate"), resolved_commit,
        evidence_manifest=attested_result.get("evidence_manifest"),
    )
    if commit_binding["decision"] == "block":
        return commit_binding
    evidence = validate_current_release_evidence(product, task_id, result)
    if evidence["decision"] == "block":
        return evidence
    return {
        "decision": "pass", "reason": "GUARDED_RELEASE_INPUTS_CURRENT",
        "commit": resolved_commit,
    }


def _record_release_block(
    result: dict[str, Any], outcome: dict[str, Any], canonical: dict[str, Any],
) -> None:
    reason = str(outcome.get("reason") or "RELEASE_READY_BLOCKED")
    result["decision"] = "block"
    result["state"] = "blocked"
    result["blockers"] = sorted(set(result.get("blockers") or []) | {reason})
    result.setdefault("cycle", {})["last_stop"] = {
        **outcome,
        "phase": "release_ready",
        "retryable": reason in RETRYABLE_RELEASE_READY_REASONS,
        "canonical_finish_input_digest": str(canonical.get("input_digest") or ""),
    }


def _restore_retryable_release_attempt(
    result: dict[str, Any], cycle: dict[str, Any], canonical: dict[str, Any],
) -> bool:
    stop = cycle.get("last_stop") or {}
    reason = str(stop.get("reason") or "")
    canonical_digest = str(canonical.get("input_digest") or "")
    if not (
        result.get("decision") == "block"
        and canonical.get("decision") == "pass"
        and canonical_digest
        and stop.get("phase") == "release_ready"
        and stop.get("retryable") is True
        and stop.get("canonical_finish_input_digest") == canonical_digest
        and reason in RETRYABLE_RELEASE_READY_REASONS
        and set(result.get("blockers") or []) == {reason}
    ):
        return False
    result["blockers"] = []
    result["decision"] = "pass"
    result["state"] = "validated"
    return True


def allow_finish_attempt(
    result: dict[str, Any], *, subject: str, policy: str, tier_floor: str,
) -> dict[str, Any]:
    readiness = finish_readiness(result)
    if readiness["decision"] != "pass":
        return readiness
    attempt = register_finish_attempt(result, canonical_digest({
        "subject": subject, "policy": policy, "tier_floor": tier_floor,
    }))
    if attempt["decision"] == "block":
        result["decision"] = "block"
        result["state"] = "blocked"
        result["blockers"] = sorted(set(result.get("blockers", [])) | {attempt["reason"]})
        result["cycle"]["last_stop"] = attempt
    return attempt


def begin_finish_span(
    result: dict[str, Any], path: Path, task_id: str, work_item_id: str,
    attempt: dict[str, Any],
) -> tuple[str, int]:
    finalize_budget = stage_budget_status(result["cycle"], "finalize")
    cycle = result.setdefault("cycle", {})
    active = cycle.get("active_finish_span") or {}
    events = read_events(ledger_path(path))
    ended = {
        str(item.get("span_id") or "")
        for item in events if item.get("event") == "end"
    }
    candidates = [
        item for item in events
        if item.get("event") == "start"
        and item.get("stage") == "finish"
        and item.get("parent_span_id") == "finalize"
        and item.get("task_id") == task_id
        and str(item.get("work_item_id") or "") == work_item_id
        and item.get("input_digest") == attempt["input_digest"]
        and str(item.get("span_id") or "") not in ended
    ]
    if active and str(active.get("span_id") or "") not in ended:
        candidates = [
            item for item in candidates
            if item.get("span_id") == active.get("span_id")
        ]
    if len(candidates) > 1:
        raise OSError("STORY_LEDGER_INVALID")
    if candidates:
        existing = candidates[0]
        existing_attempt = int(existing["attempt"])
        if int(attempt["attempt"]) != existing_attempt:
            attempts = cycle.get("finish_attempts") or []
            if attempts and attempts[-1].get("attempt") == attempt["attempt"]:
                attempts.pop()
            attempt["attempt"] = existing_attempt
        cycle["active_finish_span"] = {
            "span_id": str(existing["span_id"]),
            "started_epoch_ms": int(existing["epoch_ms"]),
            "attempt": existing_attempt,
            "input_digest": str(existing.get("input_digest") or ""),
        }
        return str(existing["span_id"]), int(existing["epoch_ms"])
    span_id, started_epoch_ms = start_span(
        ledger_path(path), task_id=task_id, stage="finish", attempt=attempt["attempt"],
        parent_span_id="finalize",
        input_digest=attempt["input_digest"],
        budget_ms=int(finalize_budget.get("remaining_ms") or 0),
        work_item_id=work_item_id,
    )
    cycle["active_finish_span"] = {
        "span_id": span_id, "started_epoch_ms": started_epoch_ms,
        "attempt": attempt["attempt"], "input_digest": attempt["input_digest"],
    }
    return span_id, started_epoch_ms


def complete_finish_span(
    result: dict[str, Any], path: Path, *, task_id: str, work_item_id: str, span_id: str,
    started_epoch_ms: int, decision: str, reason: str, attempt: dict[str, Any],
    tool_wait_ms: int | str,
) -> None:
    end_span(
        ledger_path(path), task_id=task_id, stage="finish", span_id=span_id,
        started_epoch_ms=started_epoch_ms, decision=decision, reason=reason,
        attempt=attempt["attempt"], tool_wait_ms=tool_wait_ms,
        input_digest=attempt["input_digest"], work_item_id=work_item_id,
    )
    result.setdefault("cycle", {}).pop("active_finish_span", None)
    result.setdefault("cycle", {})["canonical_finish"] = {
        "decision": decision, "reason": reason,
        "attempt": attempt["attempt"], "input_digest": attempt["input_digest"],
        "completed_at": now(),
    }
    if decision == "pass" and not result["cycle"].get("release_readback_enforced"):
        closure = close_story_cycle(result, path, task_id, work_item_id)
        if closure["decision"] != "pass":
            raise OSError(str(closure["reason"]))


def cmd_release_ready(args, *, refresh_assurance: Callable[..., None]) -> int:
    product = Path(args.product_root).resolve()
    task_id = str(args.task_id or "").strip()
    if not valid_task_id(task_id):
        dump_json({"decision": "block", "reason": "TASK_ID_INVALID"})
        return 0
    path = result_path(product, task_id)
    if not path.is_file():
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    with task_operation_lock(path):
        result = load_result(path)
        cycle = result.get("cycle") or {}
        canonical = cycle.get("canonical_finish") or {}
        retrying_release = _restore_retryable_release_attempt(result, cycle, canonical)
        if (
            (result.get("decision") == "pass" and result.get("state") == "ready_to_release")
            or retrying_release
        ) and cycle.get("ended_at"):
            work_item_id = str((result.get("work_item") or {}).get("id") or "")
            closure = _closed_cycle_status(result, path, task_id, work_item_id)
            integrity = (
                _guarded_release_integrity(
                    product, path, task_id, result, str(args.commit or "").strip(),
                )
                if closure["decision"] == "pass" else closure
            )
            receipt = result.get("lifecycle_readback") or {}
            provider = _configured_provider(result)
            replay = (
                validate_readback(
                    receipt,
                    task_id=task_id,
                    work_item_id=str((result.get("work_item") or {}).get("id") or ""),
                    provider=provider,
                    candidate_digest=str((result.get("candidate") or {}).get("digest") or ""),
                    commit=str(integrity.get("commit") or ""),
                )
                if integrity["decision"] == "pass" and isinstance(receipt, dict)
                else integrity
            )
            outcome = (
                {"decision": "pass", "reason": "STORY_ALREADY_READY_TO_RELEASE"}
                if replay["decision"] == "pass" else replay
            )
            if outcome["decision"] == "block":
                _record_release_block(
                    result, outcome, (cycle.get("canonical_finish") or {}),
                )
                atomic_write_result(path, result)
            elif retrying_release:
                result["decision"] = "pass"
                result["state"] = "ready_to_release"
                result["blockers"] = []
                cycle.pop("last_stop", None)
                atomic_write_result(path, result)
            dump_json({**outcome, "result": result})
            return 0
        if canonical.get("decision") != "pass" or (
            result.get("decision") != "pass" and not retrying_release
        ):
            outcome = {
                "decision": "block", "reason": "CANONICAL_FINISH_REQUIRED",
                "next_action": "run a passing canonical finish before guarded commit",
            }
        else:
            total = budget_status(cycle)
            finalize_budget = stage_budget_status(cycle, "finalize")
            if total["decision"] == "block":
                outcome = total
            elif finalize_budget["decision"] == "block":
                outcome = finalize_budget
            else:
                commit = str(args.commit or "").strip()
                integrity = _guarded_release_integrity(
                    product, path, task_id, result, commit,
                )
                if integrity["decision"] == "block":
                    outcome = integrity
                else:
                    resolved_commit = str(integrity["commit"])
                    work_item = result.get("work_item") or {}
                    work_item_id = str(work_item.get("id") or "")
                    provider = _configured_provider(result)
                    if provider == "noop" and not str(args.receipt or "").strip():
                        receipt = build_local_receipt(
                            task_id=task_id, work_item_id=work_item_id,
                            candidate_digest=str(result.get("candidate", {}).get("digest") or ""),
                            commit=resolved_commit,
                        )
                    else:
                        receipt_path = Path(str(args.receipt or ""))
                        if not receipt_path.is_absolute():
                            receipt_path = product / receipt_path
                        try:
                            receipt_path = receipt_path.resolve()
                            if not receipt_path.is_relative_to(product):
                                raise ValueError
                            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                            if not isinstance(receipt, dict):
                                raise ValueError
                        except (OSError, ValueError, json.JSONDecodeError):
                            receipt = {}
                    readback = validate_readback(
                        receipt, task_id=task_id, work_item_id=work_item_id,
                        provider=provider,
                        candidate_digest=str(result.get("candidate", {}).get("digest") or ""),
                        commit=resolved_commit,
                    )
                    if readback["decision"] == "block":
                        outcome = readback
                    else:
                        try:
                            stage = finish_stage(
                                result, path, task_id, "finalize", decision="pass",
                                reason="LIFECYCLE_READBACK_VALID", tool_wait_ms="unknown",
                            )
                            final_total = budget_status(cycle)
                        except OSError:
                            stage = {"decision": "block", "reason": "STORY_LEDGER_INVALID"}
                            final_total = {"decision": "block", "reason": "STORY_LEDGER_INVALID"}
                        if stage["decision"] == "block":
                            outcome = stage
                        elif final_total["decision"] == "block":
                            result["cycle"]["last_stop"] = final_total
                            outcome = final_total
                        else:
                            try:
                                closure = close_story_cycle(
                                    result, path, task_id, work_item_id,
                                )
                            except OSError:
                                closure = {
                                    "decision": "block", "reason": "STORY_LEDGER_INVALID",
                                }
                            if closure["decision"] == "pass":
                                result["lifecycle_readback"] = receipt
                                result["state"] = "ready_to_release"
                                refresh_assurance(
                                    result, product, str(result.get("policy_digest") or ""),
                                    phase="post-commit-lifecycle-readback",
                                )
                            outcome = closure
        if outcome["decision"] == "block":
            _record_release_block(result, outcome, canonical)
        atomic_write_result(path, result)
    dump_json({**outcome, "result": result})
    return 0
