#!/usr/bin/env python3
"""Story-cycle command helpers kept separate from the public command facade."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from codex_usage_receipt import automatic_receipt
from harness_assurance import refresh_result
from harness_attestation import verify_attestation
from harness_output import dump_json
from harness_runtime import (
    atomic_write_result, canonical_digest, load_result, now, policy_for, result_path, subject_for,
)
from harness_task_resolution import valid_task_id
from harness_timing import (
    STAGE_BUDGETS_MS, append_event, budget_status, end_span, initialize_cycle, ledger_path,
    parse_time, read_events, register_finish_attempt, start_span, summarize_events,
)
from harness_usage_ledger import capture_usage_baseline
from worktree_baseline import changed_since_baseline


STAGE_ORDER = tuple(STAGE_BUDGETS_MS)


def refresh_assurance(result: dict, product: Path, policy_digest: str, *, phase: str) -> None:
    refresh_result(result, product, policy_digest, phase=phase, verified_at=now(),
                   attestation=verify_attestation(
                       product, commit="HEAD", policy_digest=policy_digest,
                       task_id=str(result.get("task_id") or ""),
                   ))


def start_story_cycle(
    result: dict[str, Any], path: Path, task_id: str, work_item: dict[str, Any] | None,
) -> None:
    cycle = initialize_cycle(result)
    cycle["stage_enforced"] = True
    append_event(ledger_path(path), {
        "task_id": task_id,
        "work_item_id": str((work_item or {}).get("id") or ""),
        "stage": "story", "event": "start", "span_id": "story",
        "parent_span_id": "", "attempt": 1, "timestamp": cycle["started_at"],
        "reason": "STORY_STARTED", "budget_ms": cycle["total_budget_ms"],
    })
    span_id, epoch_ms = start_span(
        ledger_path(path), task_id=task_id, stage="takeover",
        budget_ms=cycle["stage_budgets_ms"]["takeover"],
        work_item_id=str((work_item or {}).get("id") or ""),
    )
    cycle["stages"]["takeover"] = {
        "status": "active", "wall_ms": 0,
        "attempts": [{"attempt": 1, "span_id": span_id, "started_epoch_ms": epoch_ms}],
    }
    cycle["current_stage"] = "takeover"


def begin_stage(result: dict[str, Any], path: Path, task_id: str, stage: str) -> dict[str, Any]:
    cycle = initialize_cycle(result)
    if stage not in STAGE_ORDER:
        return {"decision": "block", "reason": "STAGE_INVALID"}
    total = budget_status(cycle)
    if total["decision"] == "block":
        return {**total, "stage": stage}
    current = str(cycle.get("current_stage") or "")
    if current:
        return {"decision": "block", "reason": "STAGE_ALREADY_ACTIVE", "stage": current}
    state = cycle["stages"].setdefault(stage, {"status": "pending", "wall_ms": 0, "attempts": []})
    if state.get("status") == "pass":
        return {"decision": "block", "reason": "STAGE_ALREADY_COMPLETED", "stage": stage}
    expected = next(
        (name for name in STAGE_ORDER if (cycle["stages"].get(name) or {}).get("status") != "pass"),
        "",
    )
    if stage != expected:
        return {"decision": "block", "reason": "STAGE_DEPENDENCY_INCOMPLETE", "stage": stage}
    attempt = len(state["attempts"]) + 1
    if attempt > int(cycle["stage_retry_limit"]):
        return {"decision": "block", "reason": "STAGE_RETRY_LIMIT_EXCEEDED", "stage": stage}
    budget = int(cycle["stage_budgets_ms"][stage])
    remaining = max(0, budget - int(state.get("wall_ms") or 0))
    if remaining <= 0:
        return {"decision": "block", "reason": "STAGE_BUDGET_EXCEEDED", "stage": stage}
    work_item = result.get("work_item") or {}
    span_id, epoch_ms = start_span(
        ledger_path(path), task_id=task_id, stage=stage, attempt=attempt,
        budget_ms=remaining, work_item_id=str(work_item.get("id") or ""),
    )
    state["status"] = "active"
    state["attempts"].append({
        "attempt": attempt, "span_id": span_id, "started_epoch_ms": epoch_ms,
    })
    cycle["current_stage"] = stage
    return {"decision": "pass", "reason": "STAGE_STARTED", "stage": stage, "attempt": attempt}


def finish_stage(
    result: dict[str, Any], path: Path, task_id: str, stage: str,
    *, decision: str, reason: str, tool_wait_ms: int | str = "unknown",
) -> dict[str, Any]:
    cycle = initialize_cycle(result)
    if stage not in STAGE_ORDER or cycle.get("current_stage") != stage:
        return {"decision": "block", "reason": "STAGE_NOT_ACTIVE", "stage": stage}
    state = cycle["stages"][stage]
    attempt = state["attempts"][-1]
    event = end_span(
        ledger_path(path), task_id=task_id, stage=stage, span_id=attempt["span_id"],
        started_epoch_ms=attempt["started_epoch_ms"], decision=decision, reason=reason,
        attempt=attempt["attempt"], tool_wait_ms=tool_wait_ms,
        work_item_id=str((result.get("work_item") or {}).get("id") or ""),
    )
    attempt["wall_ms"] = event["wall_ms"]
    state["wall_ms"] = int(state.get("wall_ms") or 0) + int(event["wall_ms"])
    budget = int(cycle["stage_budgets_ms"][stage])
    if state["wall_ms"] > budget:
        decision, reason = "block", "STAGE_BUDGET_EXCEEDED"
    state.update({"status": decision, "reason": reason})
    cycle["current_stage"] = ""
    return {
        "decision": decision, "reason": reason, "stage": stage,
        "attempt": attempt["attempt"], "wall_ms": state["wall_ms"], "budget_ms": budget,
    }


def finish_readiness(result: dict[str, Any]) -> dict[str, Any]:
    cycle = result.get("cycle")
    if not isinstance(cycle, dict) or not cycle.get("stage_enforced"):
        return {"decision": "pass", "reason": "STORY_STAGE_ORDER_NOT_ENFORCED"}
    current = str(cycle.get("current_stage") or "")
    if current:
        return {"decision": "block", "reason": "STAGE_STILL_ACTIVE", "stage": current}
    incomplete = [
        stage for stage in STAGE_ORDER
        if (cycle.get("stages", {}).get(stage) or {}).get("status") != "pass"
    ]
    if incomplete:
        return {
            "decision": "block", "reason": "STAGE_DEPENDENCY_INCOMPLETE",
            "incomplete_stages": incomplete,
        }
    return {"decision": "pass", "reason": "STORY_STAGES_COMPLETE"}


def close_story_cycle(
    result: dict[str, Any], path: Path, task_id: str, work_item_id: str,
) -> dict[str, Any]:
    cycle = result.get("cycle")
    if not isinstance(cycle, dict) or not cycle.get("stage_enforced"):
        return {"decision": "pass", "reason": "STORY_STAGE_ORDER_NOT_ENFORCED"}
    readiness = finish_readiness(result)
    if readiness["decision"] != "pass":
        return readiness
    if cycle.get("ended_at"):
        return {"decision": "pass", "reason": "STORY_ALREADY_CLOSED"}
    started = int(parse_time(cycle["started_at"]).timestamp() * 1000)
    story = end_span(
        ledger_path(path), task_id=task_id, stage="story", span_id="story",
        started_epoch_ms=started, decision="pass", reason="STORY_READY_TO_RELEASE",
        work_item_id=work_item_id,
    )
    cycle["ended_at"] = story["timestamp"]
    cycle["root_wall_ms"] = story["wall_ms"]
    return {"decision": "pass", "reason": "STORY_READY_TO_RELEASE"}


def cmd_stage(args) -> int:
    product = Path(args.product_root).resolve()
    path = result_path(product, args.task_id)
    if not valid_task_id(args.task_id) or not path.is_file():
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    result = load_result(path)
    if args.action == "status":
        outcome = {
            "decision": budget_status(result["cycle"])["decision"],
            "reason": "STAGE_STATUS", "cycle": result["cycle"],
            "ledger": summarize_events(read_events(ledger_path(path))),
        }
    elif args.action == "start":
        outcome = begin_stage(result, path, args.task_id, args.stage)
    else:
        wait = int(args.tool_wait_ms) if str(args.tool_wait_ms).isdigit() else "unknown"
        outcome = finish_stage(
            result, path, args.task_id, args.stage, decision=args.decision,
            reason=args.reason, tool_wait_ms=wait,
        )
    if args.action != "status":
        atomic_write_result(path, result)
    dump_json(outcome)
    return 0


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
    return start_span(
        ledger_path(path), task_id=task_id, stage="finish", attempt=attempt["attempt"],
        parent_span_id="finalize",
        input_digest=attempt["input_digest"],
        budget_ms=result["cycle"]["stage_budgets_ms"]["finalize"],
        work_item_id=work_item_id,
    )


def complete_finish_span(
    result: dict[str, Any], path: Path, *, task_id: str, work_item_id: str, span_id: str,
    started_epoch_ms: int, decision: str, reason: str, attempt: dict[str, Any],
    tool_wait_ms: int,
) -> None:
    end_span(
        ledger_path(path), task_id=task_id, stage="finish", span_id=span_id,
        started_epoch_ms=started_epoch_ms, decision=decision, reason=reason,
        attempt=attempt["attempt"], tool_wait_ms=tool_wait_ms,
        input_digest=attempt["input_digest"], work_item_id=work_item_id,
    )
    if decision == "pass":
        closure = close_story_cycle(result, path, task_id, work_item_id)
        if closure["decision"] != "pass":
            raise OSError(str(closure["reason"]))


def cmd_usage_baseline(args) -> int:
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    if not valid_task_id(args.task_id):
        dump_json({"decision": "block", "reason": "TASK_ID_INVALID"})
        return 0
    path = result_path(product, args.task_id)
    reason = str(args.reason or "").strip()
    if not path.is_file():
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    if not reason:
        dump_json({"decision": "block", "reason": "USAGE_BASELINE_REASON_REQUIRED"})
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
