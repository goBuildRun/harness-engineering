#!/usr/bin/env python3
"""Privacy-safe Story wall-clock ledger, budgets, and retry ceilings."""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ael_story_ledger import read_events, valid_event


SCHEMA = "harness-story-wall-clock-v1"
UNKNOWN = "unknown"
TOTAL_BUDGET_MS = 30 * 60 * 1000
STAGE_BUDGETS_MS = {
    "takeover": 2 * 60 * 1000, "planning": 3 * 60 * 1000,
    "implementation_test": 10 * 60 * 1000, "independent_qa": 7 * 60 * 1000,
    "deploy_provider": 5 * 60 * 1000, "finalize": 3 * 60 * 1000,
}
FINISH_RETRY_LIMIT = 2
STAGE_RETRY_LIMIT = 2
REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]*(?::[A-Z0-9_.-]+)?$")
EVENT_FIELDS = frozenset({
    "task_id", "work_item_id", "stage", "event", "span_id", "parent_span_id", "attempt",
    "timestamp", "epoch_ms", "wall_ms", "tool_wait_ms", "active_ms", "decision", "reason",
    "input_digest", "cache_hit", "budget_ms",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def _safe_reason(value: str) -> str:
    reason = value.strip() or "UNKNOWN"
    return reason if REASON_CODE.fullmatch(reason) else "UNSTRUCTURED_REASON_REDACTED"

def initialize_cycle(result: dict[str, Any], *, started_at: str | None = None) -> dict[str, Any]:
    cycle = result.setdefault("cycle", {})
    start = started_at or cycle.get("started_at") or utc_now()
    cycle.setdefault("schema", SCHEMA)
    if started_at is not None:
        cycle["confirmed_at"] = start
        cycle["started_at"] = start
    else:
        cycle.setdefault("confirmed_at", start)
        cycle.setdefault("started_at", start)
    try:
        deadline = parse_time(str(cycle["started_at"])) + timedelta(milliseconds=TOTAL_BUDGET_MS)
        deadline_at = deadline.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (TypeError, ValueError):
        deadline_at = "unknown"
    if started_at is not None:
        cycle["deadline_at"] = deadline_at
        cycle["last_observed_at"] = cycle["started_at"]
    else:
        cycle.setdefault("deadline_at", deadline_at)
        cycle.setdefault("last_observed_at", cycle["started_at"])
    cycle.setdefault("total_budget_ms", TOTAL_BUDGET_MS)
    cycle.setdefault("stage_budgets_ms", dict(STAGE_BUDGETS_MS))
    cycle.setdefault("finish_retry_limit", FINISH_RETRY_LIMIT)
    cycle.setdefault("stage_retry_limit", STAGE_RETRY_LIMIT)
    cycle.setdefault("stages", {})
    cycle.setdefault("finish_attempts", [])
    cycle.setdefault("agent_active_ms", UNKNOWN)
    return cycle

def ledger_path(task_result_path: Path) -> Path:
    return task_result_path.parent / "wall-clock-ledger.jsonl"

def _clean_event(event: dict[str, Any]) -> dict[str, Any]:
    clean = {key: event[key] for key in EVENT_FIELDS if key in event}
    clean.update({"schema": SCHEMA})
    clean.setdefault("span_id", uuid.uuid4().hex)
    clean.setdefault("attempt", 1)
    clean["reason"] = _safe_reason(str(clean.get("reason") or "UNKNOWN"))
    clean.setdefault("timestamp", utc_now())
    clean.setdefault("epoch_ms", int(time.time() * 1000))
    if clean.get("event") == "end":
        clean.setdefault("wall_ms", 0)
        clean.setdefault("decision", "block")
    if clean.get("active_ms") is None:
        clean["active_ms"] = UNKNOWN
    return clean

def append_event(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    clean = _clean_event(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        _validate_locked_ledger(stream)
        stream.seek(0, os.SEEK_END)
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return clean

def _validate_locked_ledger(stream) -> None:
    stream.seek(0)
    raw = stream.read()
    if raw and not raw.endswith("\n"):
        raise OSError("STORY_LEDGER_INVALID")
    try:
        events = [json.loads(line) for line in raw.splitlines()]
    except json.JSONDecodeError as exc:
        raise OSError("STORY_LEDGER_INVALID") from exc
    if any(not valid_event(event) for event in events):
        raise OSError("STORY_LEDGER_INVALID")

def _append_end_event_once(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        _validate_locked_ledger(stream)
        stream.seek(0)
        for line in stream:
            existing = json.loads(line)
            if (isinstance(existing, dict) and existing.get("schema") == SCHEMA
                    and existing.get("event") == "end"
                    and existing.get("span_id") == event.get("span_id")):
                replay_fields = (
                    "task_id", "work_item_id", "stage", "span_id", "attempt",
                    "decision", "reason", "input_digest",
                )
                expected = _clean_event(event)
                if any(existing.get(key) != expected.get(key) for key in replay_fields):
                    raise OSError("STORY_LEDGER_REPLAY_CONFLICT")
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                return {**existing, "_replayed": True}
        clean = _clean_event(event)
        stream.seek(0, os.SEEK_END)
        stream.write(json.dumps(clean, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return clean

def start_span(
    path: Path,
    *,
    task_id: str,
    stage: str,
    attempt: int = 1,
    parent_span_id: str = "story",
    input_digest: str = "",
    budget_ms: int | None = None,
    work_item_id: str = "",
) -> tuple[str, int]:
    span_id = uuid.uuid4().hex
    epoch_ms = int(time.time() * 1000)
    append_event(path, {
        "task_id": task_id,
        "work_item_id": work_item_id,
        "stage": stage,
        "event": "start",
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "attempt": attempt,
        "epoch_ms": epoch_ms,
        "input_digest": input_digest,
        "budget_ms": budget_ms if budget_ms is not None else STAGE_BUDGETS_MS.get(stage, 0),
        "reason": "SPAN_STARTED",
    })
    return span_id, epoch_ms

def end_span(
    path: Path,
    *,
    task_id: str,
    stage: str,
    span_id: str,
    started_epoch_ms: int,
    decision: str,
    reason: str,
    attempt: int = 1,
    tool_wait_ms: int | str = UNKNOWN,
    active_ms: int | str = UNKNOWN,
    input_digest: str = "",
    cache_hit: bool = False,
    work_item_id: str = "",
    ended_epoch_ms: int | None = None,
) -> dict[str, Any]:
    ended_ms = ended_epoch_ms if ended_epoch_ms is not None else int(time.time() * 1000)
    return _append_end_event_once(path, {
        "task_id": task_id,
        "work_item_id": work_item_id,
        "stage": stage,
        "event": "end",
        "span_id": span_id,
        "attempt": attempt,
        "epoch_ms": ended_ms,
        "wall_ms": max(0, ended_ms - started_epoch_ms),
        "tool_wait_ms": tool_wait_ms,
        "active_ms": active_ms,
        "decision": decision,
        "reason": reason,
        "input_digest": input_digest,
        "cache_hit": cache_hit,
    })

def elapsed_ms(cycle: dict[str, Any], *, at: str | None = None) -> int | None:
    try:
        start = parse_time(str(cycle["started_at"]))
        end = parse_time(at) if at else datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    return max(0, int((end - start).total_seconds() * 1000))

def budget_status(cycle: dict[str, Any], *, at: str | None = None) -> dict[str, Any]:
    budget = int(cycle.get("total_budget_ms") or TOTAL_BUDGET_MS)
    try:
        start = parse_time(str(cycle["started_at"]))
        end = parse_time(at) if at else datetime.now(timezone.utc)
        last = parse_time(str(cycle.get("last_observed_at") or cycle["started_at"]))
        if start.tzinfo is None or end.tzinfo is None or last.tzinfo is None:
            raise ValueError("naive Story clock")
        if end < start or end < last:
            return {
                "decision": "block", "reason": "STORY_CLOCK_ROLLBACK",
                "budget_ms": budget, "elapsed_ms": UNKNOWN, "remaining_ms": 0,
                "next_action": "repair Story clock metadata before another controlled action",
            }
        used = max(0, int((end - start).total_seconds() * 1000))
    except (KeyError, TypeError, ValueError):
        return {
            "decision": "block", "reason": "STORY_CLOCK_INVALID",
            "budget_ms": budget, "elapsed_ms": UNKNOWN, "remaining_ms": UNKNOWN,
            "next_action": "repair Story clock metadata before another controlled action",
        }
    cycle["last_observed_at"] = end.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    remaining = max(0, budget - used)
    return {
        "decision": "pass" if used < budget else "block",
        "reason": "STORY_BUDGET_OK" if used < budget else "STORY_BUDGET_EXCEEDED",
        "budget_ms": budget,
        "elapsed_ms": used,
        "remaining_ms": remaining,
        "next_action": (
            "continue current bounded stage" if used < budget
            else "stop before another gate or Provider side effect and escalate"
        ),
    }

def stage_budget_status(
    cycle: dict[str, Any], stage: str | None = None, *, epoch_ms: int | None = None,
) -> dict[str, Any]:
    stage = stage or str(cycle.get("current_stage") or "")
    if stage not in STAGE_BUDGETS_MS:
        return {"decision": "block", "reason": "STAGE_NOT_ACTIVE", "stage": stage}
    state = (cycle.get("stages") or {}).get(stage) or {}
    budget = int((cycle.get("stage_budgets_ms") or {}).get(stage) or STAGE_BUDGETS_MS[stage])
    used = int(state.get("wall_ms") or 0)
    attempts = state.get("attempts") or []
    if state.get("status") == "active" and attempts:
        current = epoch_ms if epoch_ms is not None else int(time.time() * 1000)
        used += max(0, current - int(attempts[-1].get("started_epoch_ms") or current))
    remaining = max(0, budget - used)
    return {
        "decision": "pass" if used < budget else "block",
        "reason": "STAGE_BUDGET_OK" if used < budget else "STAGE_BUDGET_EXCEEDED",
        "stage": stage,
        "attempt": len(attempts),
        "budget_ms": budget,
        "elapsed_ms": used,
        "remaining_ms": remaining,
        "next_action": (
            "continue current bounded stage" if used < budget
            else "stop the stage and emit a bounded-repair or escalation receipt"
        ),
    }

def register_finish_attempt(
    result: dict[str, Any], input_digest: str, *, at: str | None = None,
) -> dict[str, Any]:
    cycle = initialize_cycle(result)
    budget = budget_status(cycle, at=at)
    if budget["decision"] == "block":
        return {**budget, "attempt": 0, "retry_limit": cycle["finish_retry_limit"]}
    attempts = cycle["finish_attempts"]
    matching = [item for item in attempts if item.get("input_digest") == input_digest]
    attempt = len(matching) + 1
    limit = int(cycle.get("finish_retry_limit") or FINISH_RETRY_LIMIT)
    if attempt > limit:
        return {
            "decision": "block", "reason": "FINISH_RETRY_LIMIT_EXCEEDED",
            "attempt": attempt, "retry_limit": limit, "input_digest": input_digest,
            **{key: budget[key] for key in ("budget_ms", "elapsed_ms", "remaining_ms")},
        }
    attempts.append({"attempt": attempt, "input_digest": input_digest, "started_at": at or utc_now()})
    return {
        "decision": "pass", "reason": "FINISH_ATTEMPT_ALLOWED",
        "attempt": attempt, "retry_limit": limit, "input_digest": input_digest,
        **{key: budget[key] for key in ("budget_ms", "elapsed_ms", "remaining_ms")},
    }


def _union_duration(intervals: list[tuple[int, int]]) -> int:
    total = 0
    end = -1
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        if start >= end:
            total += stop - start
        elif stop > end:
            total += stop - end
        end = max(end, stop)
    return total


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    starts = {
        str(item.get("span_id")): item for item in events
        if item.get("event") == "start" and item.get("span_id")
    }
    spans: list[dict[str, Any]] = []
    incomplete = set(starts)
    duplicates: list[str] = []
    completed: set[str] = set()
    for item in events:
        span_id = str(item.get("span_id") or "")
        if item.get("event") != "end" or span_id not in starts:
            continue
        if span_id in completed:
            duplicates.append(span_id)
            continue
        start = starts[span_id]
        begin, end = int(start.get("epoch_ms") or 0), int(item.get("epoch_ms") or 0)
        spans.append({
            "span_id": span_id,
            "stage": item.get("stage") or start.get("stage"),
            "started_epoch_ms": begin,
            "ended_epoch_ms": end,
            "wall_ms": max(0, end - begin),
            "tool_wait_ms": item.get("tool_wait_ms", UNKNOWN),
            "active_ms": item.get("active_ms", UNKNOWN),
            "decision": item.get("decision", "block"),
            "reason": item.get("reason", "UNKNOWN"),
        })
        incomplete.discard(span_id)
        completed.add(span_id)
    intervals = [(item["started_epoch_ms"], item["ended_epoch_ms"]) for item in spans]
    wall = (max(end for _start, end in intervals) - min(start for start, _end in intervals)) if intervals else 0
    classified_intervals = [
        (item["started_epoch_ms"], item["ended_epoch_ms"])
        for item in spans
        if isinstance(item.get("tool_wait_ms"), int)
        and item["tool_wait_ms"] in {0, item["wall_ms"]}
    ]
    proven_wait_intervals = [
        (item["started_epoch_ms"], item["ended_epoch_ms"])
        for item in spans
        if isinstance(item.get("tool_wait_ms"), int)
        and item["tool_wait_ms"] == item["wall_ms"]
        and item["wall_ms"] > 0
    ]
    return {
        "schema": SCHEMA,
        "span_count": len(spans),
        "root_wall_ms": wall,
        "covered_wall_ms": _union_duration(intervals),
        "tool_wait_union_ms": _union_duration(proven_wait_intervals),
        "unproven_tool_wait_ms": (
            UNKNOWN
            if incomplete or _union_duration(classified_intervals) < wall
            else 0
        ),
        "agent_active_ms": UNKNOWN,
        "incomplete_span_ids": sorted(incomplete),
        "duplicate_end_span_ids": sorted(set(duplicates)),
        "ledger_integrity": "block" if duplicates else "pass",
        "spans": spans,
    }


class measured_span:
    """Context manager used by gate workers without recording sensitive inputs."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.started = 0.0
        self.duration_ms = 0

    def __enter__(self) -> "measured_span":
        self.started = self.clock()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.duration_ms = max(0, int((self.clock() - self.started) * 1000))
