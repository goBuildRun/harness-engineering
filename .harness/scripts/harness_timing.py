#!/usr/bin/env python3
"""Privacy-safe Story wall-clock ledger, budgets, and retry ceilings."""
from __future__ import annotations

import fcntl
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCHEMA = "harness-story-wall-clock-v1"
UNKNOWN = "unknown"
TOTAL_BUDGET_MS = 30 * 60 * 1000
STAGE_BUDGETS_MS = {
    "takeover": 2 * 60 * 1000,
    "planning": 3 * 60 * 1000,
    "implementation_test": 10 * 60 * 1000,
    "independent_qa": 7 * 60 * 1000,
    "deploy_provider": 5 * 60 * 1000,
    "finalize": 3 * 60 * 1000,
}
FINISH_RETRY_LIMIT = 2
STAGE_RETRY_LIMIT = 2
REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]*(?::[A-Z0-9_.-]+)?$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_reason(value: str) -> str:
    reason = value.strip() or "UNKNOWN"
    return reason if REASON_CODE.fullmatch(reason) else "UNSTRUCTURED_REASON_REDACTED"


def initialize_cycle(result: dict[str, Any], *, started_at: str | None = None) -> dict[str, Any]:
    cycle = result.setdefault("cycle", {})
    cycle.setdefault("schema", SCHEMA)
    cycle.setdefault("started_at", started_at or utc_now())
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


def append_event(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "task_id", "work_item_id", "stage", "event", "span_id", "parent_span_id",
        "attempt", "timestamp", "epoch_ms", "wall_ms", "tool_wait_ms", "active_ms",
        "decision", "reason", "input_digest", "cache_hit", "budget_ms",
    }
    clean = {key: event[key] for key in allowed if key in event}
    clean.update({"schema": SCHEMA})
    clean["reason"] = _safe_reason(str(clean.get("reason") or "UNKNOWN"))
    clean.setdefault("timestamp", utc_now())
    clean.setdefault("epoch_ms", int(time.time() * 1000))
    if clean.get("active_ms") is None:
        clean["active_ms"] = UNKNOWN
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(line)
        stream.flush()
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
) -> dict[str, Any]:
    ended_ms = int(time.time() * 1000)
    return append_event(path, {
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
    used = elapsed_ms(cycle, at=at)
    budget = int(cycle.get("total_budget_ms") or TOTAL_BUDGET_MS)
    if used is None:
        return {
            "decision": "block", "reason": "STORY_CLOCK_INVALID",
            "budget_ms": budget, "elapsed_ms": UNKNOWN, "remaining_ms": UNKNOWN,
        }
    remaining = max(0, budget - used)
    return {
        "decision": "pass" if used < budget else "block",
        "reason": "STORY_BUDGET_OK" if used < budget else "STORY_BUDGET_EXCEEDED",
        "budget_ms": budget,
        "elapsed_ms": used,
        "remaining_ms": remaining,
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
    incomplete = sorted(starts)
    for item in events:
        span_id = str(item.get("span_id") or "")
        if item.get("event") != "end" or span_id not in starts:
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
        incomplete.remove(span_id)
    intervals = [(item["started_epoch_ms"], item["ended_epoch_ms"]) for item in spans]
    wall = (max(end for _start, end in intervals) - min(start for start, _end in intervals)) if intervals else 0
    return {
        "schema": SCHEMA,
        "span_count": len(spans),
        "root_wall_ms": wall,
        "covered_wall_ms": _union_duration(intervals),
        "tool_wait_union_ms": (
            UNKNOWN if any(
                isinstance(item.get("tool_wait_ms"), int)
                and item["tool_wait_ms"] != item["wall_ms"] for item in spans
            ) else _union_duration([
                (item["started_epoch_ms"], item["ended_epoch_ms"])
                for item in spans
                if isinstance(item.get("tool_wait_ms"), int)
                and item["tool_wait_ms"] == item["wall_ms"]
            ])
        ),
        "agent_active_ms": UNKNOWN,
        "incomplete_span_ids": incomplete,
        "spans": spans,
    }


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("schema") == SCHEMA:
            events.append(item)
    return events


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
