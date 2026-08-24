#!/usr/bin/env python3
"""Story-cycle stage state transitions and wall-clock closure."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ael_timing import (
    STAGE_BUDGETS_MS, append_event, budget_status, end_span, initialize_cycle, ledger_path,
    parse_time, read_events, stage_budget_status, start_span,
)


STAGE_ORDER = tuple(STAGE_BUDGETS_MS)


def _orphan_stage_span(
    result: dict[str, Any], path: Path, task_id: str, stage: str,
) -> str:
    known = {
        str(attempt.get("span_id") or "")
        for state in (result.get("cycle", {}).get("stages") or {}).values()
        for attempt in (state.get("attempts") or [])
        if isinstance(attempt, dict)
    }
    for event in read_events(ledger_path(path)):
        span_id = str(event.get("span_id") or "")
        if (
            event.get("event") == "start"
            and event.get("task_id") == task_id
            and event.get("stage") == stage
            and event.get("parent_span_id") == "story"
            and span_id
            and span_id not in known
        ):
            return span_id
    return ""


def stage_start_readiness(
    result: dict[str, Any], path: Path, task_id: str, stage: str,
) -> dict[str, Any]:
    cycle = initialize_cycle(result)
    if stage not in STAGE_ORDER:
        return {"decision": "block", "reason": "STAGE_INVALID"}
    try:
        orphan = _orphan_stage_span(result, path, task_id, stage)
    except OSError:
        return {"decision": "block", "reason": "STORY_LEDGER_INVALID", "stage": stage}
    if orphan:
        return {
            "decision": "block", "reason": "STAGE_ORPHAN_SPAN_DETECTED",
            "stage": stage, "span_id": orphan,
            "next_action": "repair the persisted stage state before retrying",
        }
    total = budget_status(cycle)
    if total["decision"] == "block":
        return {**total, "stage": stage}
    current = str(cycle.get("current_stage") or "")
    if current:
        return {
            **stage_budget_status(cycle, current), "decision": "block",
            "reason": "STAGE_ALREADY_ACTIVE", "stage": current,
        }
    state = cycle["stages"].setdefault(stage, {"status": "pending", "wall_ms": 0, "attempts": []})
    if state.get("status") == "pass":
        return {
            **stage_budget_status(cycle, stage), "decision": "block",
            "reason": "STAGE_ALREADY_COMPLETED", "stage": stage,
        }
    expected = next(
        (name for name in STAGE_ORDER if (cycle["stages"].get(name) or {}).get("status") != "pass"),
        "",
    )
    if stage != expected:
        return {
            "decision": "block", "reason": "STAGE_DEPENDENCY_INCOMPLETE",
            "stage": stage, "attempt": len(state["attempts"]),
            "budget_ms": int(cycle["stage_budgets_ms"][stage]),
            "elapsed_ms": int(state.get("wall_ms") or 0),
            "next_action": f"complete {expected} before starting {stage}",
        }
    attempt = len(state["attempts"]) + 1
    if attempt > int(cycle["stage_retry_limit"]):
        return {
            "decision": "block", "reason": "STAGE_RETRY_LIMIT_EXCEEDED",
            "stage": stage, "attempt": attempt,
            "budget_ms": int(cycle["stage_budgets_ms"][stage]),
            "elapsed_ms": int(state.get("wall_ms") or 0),
            "next_action": "stop automatic repair and escalate with the last blocker",
        }
    budget = int(cycle["stage_budgets_ms"][stage])
    remaining = max(0, budget - int(state.get("wall_ms") or 0))
    if remaining <= 0:
        return {**stage_budget_status(cycle, stage), "attempt": attempt}
    return {
        "decision": "pass", "reason": "STAGE_START_ALLOWED", "stage": stage,
        "attempt": attempt, "budget_ms": budget,
        "elapsed_ms": int(state.get("wall_ms") or 0), "remaining_ms": remaining,
    }


def start_story_cycle(
    result: dict[str, Any], path: Path, task_id: str, work_item: dict[str, Any] | None,
    *, confirmed_at: str | None = None,
) -> None:
    cycle = initialize_cycle(result, started_at=confirmed_at)
    cycle["stage_enforced"] = True
    cycle["release_readback_enforced"] = (
        str((result.get("task") or {}).get("confirmation") or "") == "explicit"
    )
    events = read_events(ledger_path(path))
    story_starts = [
        item for item in events
        if item.get("event") == "start" and item.get("span_id") == "story"
    ]
    work_item_id = str((work_item or {}).get("id") or "")
    if len(story_starts) > 1 or (
        story_starts and (
            story_starts[0].get("task_id") != task_id
            or str(story_starts[0].get("work_item_id") or "") != work_item_id
            or story_starts[0].get("stage") != "story"
            or story_starts[0].get("attempt") != 1
        )
    ):
        raise OSError("STORY_LEDGER_REPLAY_CONFLICT")
    if story_starts:
        started_at = str(story_starts[0].get("timestamp") or "")
        try:
            timestamp_epoch_ms = int(parse_time(started_at).timestamp() * 1000)
        except (TypeError, ValueError):
            raise OSError("STORY_LEDGER_INVALID") from None
        if timestamp_epoch_ms != int(story_starts[0]["epoch_ms"]):
            raise OSError("STORY_LEDGER_REPLAY_CONFLICT")
        cycle = initialize_cycle(result, started_at=started_at)
        cycle["stage_enforced"] = True
        cycle["release_readback_enforced"] = (
            str((result.get("task") or {}).get("confirmation") or "") == "explicit"
        )
    else:
        append_event(ledger_path(path), {
            "task_id": task_id,
            "work_item_id": work_item_id,
            "stage": "story", "event": "start", "span_id": "story",
            "parent_span_id": "", "attempt": 1, "timestamp": cycle["started_at"],
            "epoch_ms": int(parse_time(cycle["started_at"]).timestamp() * 1000),
            "reason": "STORY_CONFIRMED", "budget_ms": cycle["total_budget_ms"],
        })
    if cycle.get("current_stage") or cycle.get("stages", {}).get("takeover"):
        return
    ended = {
        str(item.get("span_id") or "")
        for item in events if item.get("event") == "end"
    }
    orphan_takeovers = [
        item for item in events
        if item.get("event") == "start"
        and item.get("stage") == "takeover"
        and item.get("parent_span_id") == "story"
        and item.get("task_id") == task_id
        and str(item.get("work_item_id") or "") == work_item_id
        and str(item.get("span_id") or "") not in ended
    ]
    if len(orphan_takeovers) > 1:
        raise OSError("STORY_LEDGER_INVALID")
    if orphan_takeovers:
        orphan = orphan_takeovers[0]
        cycle["stages"]["takeover"] = {
            "status": "active", "wall_ms": 0,
            "attempts": [{
                "attempt": int(orphan["attempt"]),
                "span_id": str(orphan["span_id"]),
                "started_epoch_ms": int(orphan["epoch_ms"]),
            }],
        }
        cycle["current_stage"] = "takeover"
        return
    span_id, epoch_ms = start_span(
        ledger_path(path), task_id=task_id, stage="takeover",
        budget_ms=cycle["stage_budgets_ms"]["takeover"],
        work_item_id=work_item_id,
    )
    cycle["stages"]["takeover"] = {
        "status": "active", "wall_ms": 0,
        "attempts": [{"attempt": 1, "span_id": span_id, "started_epoch_ms": epoch_ms}],
    }
    cycle["current_stage"] = "takeover"


def begin_stage(result: dict[str, Any], path: Path, task_id: str, stage: str) -> dict[str, Any]:
    readiness = stage_start_readiness(result, path, task_id, stage)
    if readiness["decision"] == "block":
        return readiness
    cycle = result["cycle"]
    state = cycle["stages"][stage]
    attempt = int(readiness["attempt"])
    budget = int(readiness["budget_ms"])
    remaining = int(readiness["remaining_ms"])
    work_item = result.get("work_item") or {}
    try:
        span_id, epoch_ms = start_span(
            ledger_path(path), task_id=task_id, stage=stage, attempt=attempt,
            budget_ms=remaining, work_item_id=str(work_item.get("id") or ""),
        )
    except OSError:
        return {"decision": "block", "reason": "STORY_LEDGER_INVALID", "stage": stage}
    state["status"] = "active"
    state["attempts"].append({
        "attempt": attempt, "span_id": span_id, "started_epoch_ms": epoch_ms,
    })
    cycle["current_stage"] = stage
    return {
        "decision": "pass", "reason": "STAGE_STARTED", "stage": stage,
        "attempt": attempt, "budget_ms": budget,
        "elapsed_ms": int(state.get("wall_ms") or 0), "remaining_ms": remaining,
    }


def finish_stage(
    result: dict[str, Any], path: Path, task_id: str, stage: str,
    *, decision: str, reason: str, tool_wait_ms: int | str = "unknown",
) -> dict[str, Any]:
    cycle = initialize_cycle(result)
    if stage not in STAGE_ORDER or cycle.get("current_stage") != stage:
        return {"decision": "block", "reason": "STAGE_NOT_ACTIVE", "stage": stage}
    state = cycle["stages"][stage]
    attempt = state["attempts"][-1]
    existing_ends = [
        event for event in read_events(ledger_path(path))
        if event.get("event") == "end" and event.get("span_id") == attempt["span_id"]
    ]
    if len(existing_ends) > 1:
        raise OSError("STORY_LEDGER_REPLAY_CONFLICT")
    if existing_ends:
        persisted = existing_ends[0]
        ended_epoch_ms = int(persisted["epoch_ms"])
        decision = str(persisted.get("decision") or "block")
        reason = str(persisted.get("reason") or "UNKNOWN")
    else:
        ended_epoch_ms = int(time.time() * 1000)
        stage_budget = stage_budget_status(cycle, stage, epoch_ms=ended_epoch_ms)
        if stage_budget["decision"] == "block":
            decision, reason = "block", "STAGE_BUDGET_EXCEEDED"
    event = end_span(
        ledger_path(path), task_id=task_id, stage=stage, span_id=attempt["span_id"],
        started_epoch_ms=attempt["started_epoch_ms"], decision=decision, reason=reason,
        attempt=attempt["attempt"], tool_wait_ms=tool_wait_ms,
        work_item_id=str((result.get("work_item") or {}).get("id") or ""),
        ended_epoch_ms=ended_epoch_ms,
    )
    replayed = event.get("_replayed") is True
    decision = str(event.get("decision") or "block")
    reason = str(event.get("reason") or "UNKNOWN")
    prior_attempt_wall = attempt.get("wall_ms")
    attempt["wall_ms"] = int(event["wall_ms"])
    if prior_attempt_wall is None:
        state["wall_ms"] = int(state.get("wall_ms") or 0) + int(event["wall_ms"])
    budget = int(cycle["stage_budgets_ms"][stage])
    if not replayed and state["wall_ms"] >= budget:
        decision, reason = "block", "STAGE_BUDGET_EXCEEDED"
    state.update({"status": decision, "reason": reason})
    cycle["current_stage"] = ""
    return {
        "decision": decision, "reason": reason, "stage": stage,
        "attempt": attempt["attempt"], "wall_ms": state["wall_ms"],
        "elapsed_ms": state["wall_ms"], "budget_ms": budget,
        "remaining_ms": max(0, budget - state["wall_ms"]),
        "next_action": (
            "start the next dependent stage" if decision == "pass"
            else "stop automatic repair and escalate with the stage receipt"
        ),
    }


def finish_readiness(result: dict[str, Any]) -> dict[str, Any]:
    cycle = result.get("cycle")
    if not isinstance(cycle, dict) or not cycle.get("stage_enforced"):
        return {"decision": "pass", "reason": "STORY_STAGE_ORDER_NOT_ENFORCED"}
    current = str(cycle.get("current_stage") or "")
    if not cycle.get("release_readback_enforced"):
        if current:
            return {"decision": "block", "reason": "STAGE_STILL_ACTIVE", "stage": current}
        incomplete = [
            stage for stage in STAGE_ORDER
            if (cycle.get("stages", {}).get(stage) or {}).get("status") != "pass"
        ]
        return (
            {"decision": "pass", "reason": "STORY_STAGES_COMPLETE"}
            if not incomplete else {
                "decision": "block", "reason": "STAGE_DEPENDENCY_INCOMPLETE",
                "incomplete_stages": incomplete,
            }
        )
    if current != "finalize":
        return {
            "decision": "block",
            "reason": "STAGE_STILL_ACTIVE" if current else "FINALIZE_STAGE_NOT_ACTIVE",
            "stage": current or "finalize",
            "next_action": "start finalize after deploy_provider passes",
        }
    incomplete = [
        stage for stage in STAGE_ORDER[:-1]
        if (cycle.get("stages", {}).get(stage) or {}).get("status") != "pass"
    ]
    if incomplete:
        return {
            "decision": "block", "reason": "STAGE_DEPENDENCY_INCOMPLETE",
            "incomplete_stages": incomplete,
        }
    finalize_state = (cycle.get("stages") or {}).get("finalize") or {}
    if finalize_state.get("status") != "active":
        return {
            "decision": "block", "reason": "FINALIZE_STAGE_NOT_ACTIVE",
            "stage": "finalize", "next_action": "start finalize before finish",
        }
    return {"decision": "pass", "reason": "CANONICAL_FINISH_READY"}


def close_story_cycle(
    result: dict[str, Any], path: Path, task_id: str, work_item_id: str,
) -> dict[str, Any]:
    cycle = result.get("cycle")
    if not isinstance(cycle, dict) or not cycle.get("stage_enforced"):
        return {"decision": "pass", "reason": "STORY_STAGE_ORDER_NOT_ENFORCED"}
    if not cycle.get("release_readback_enforced"):
        readiness = finish_readiness(result)
        if readiness["decision"] != "pass":
            return readiness
    if any(
        (cycle.get("stages", {}).get(stage) or {}).get("status") != "pass"
        for stage in STAGE_ORDER
    ):
        return {
            "decision": "block", "reason": "STAGE_DEPENDENCY_INCOMPLETE",
            "next_action": "complete canonical finish, commit, and lifecycle readback",
        }
    if cycle.get("ended_at"):
        return {"decision": "pass", "reason": "STORY_ALREADY_CLOSED"}
    events = read_events(ledger_path(path))
    story_ends = [
        item for item in events
        if item.get("event") == "end" and item.get("span_id") == "story"
    ]
    if len(story_ends) > 1:
        return {"decision": "block", "reason": "STORY_LEDGER_REPLAY_CONFLICT"}
    existing_story_end = story_ends[0] if story_ends else None
    if existing_story_end is not None:
        started = int(parse_time(cycle["started_at"]).timestamp() * 1000)
        ended_epoch_ms = int(existing_story_end["epoch_ms"])
        wall_ms = int(existing_story_end["wall_ms"])
        identity_matches = (
            existing_story_end.get("task_id") == task_id
            and str(existing_story_end.get("work_item_id") or "") == work_item_id
            and existing_story_end.get("stage") == "story"
            and existing_story_end.get("attempt") == 1
            and ended_epoch_ms - wall_ms == started
            and any(
                item.get("event") == "start"
                and item.get("span_id") == "story"
                and item.get("task_id") == task_id
                and str(item.get("work_item_id") or "") == work_item_id
                and item.get("stage") == "story"
                and item.get("attempt") == 1
                and int(item.get("epoch_ms") or -1) == started
                for item in events
            )
        )
        if not identity_matches:
            return {"decision": "block", "reason": "STORY_LEDGER_REPLAY_CONFLICT"}
        if existing_story_end.get("decision") != "pass":
            return {
                "decision": "block",
                "reason": str(existing_story_end.get("reason") or "STORY_CLOSE_FAILED"),
            }
        cycle["ended_at"] = str(existing_story_end.get("timestamp") or "unknown")
        cycle["root_wall_ms"] = int(existing_story_end.get("wall_ms") or 0)
        return {"decision": "pass", "reason": "STORY_READY_TO_RELEASE"}
    started = int(parse_time(cycle["started_at"]).timestamp() * 1000)
    total = budget_status(cycle)
    if total["decision"] == "block":
        return total
    story = end_span(
        ledger_path(path), task_id=task_id, stage="story", span_id="story",
        started_epoch_ms=started, decision="pass", reason="STORY_READY_TO_RELEASE",
        work_item_id=work_item_id,
    )
    if story.get("decision") != "pass":
        return {
            "decision": "block", "reason": str(story.get("reason") or "STORY_CLOSE_FAILED"),
        }
    cycle["ended_at"] = story["timestamp"]
    cycle["root_wall_ms"] = story["wall_ms"]
    return {"decision": "pass", "reason": "STORY_READY_TO_RELEASE"}
