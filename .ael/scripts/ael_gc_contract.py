#!/usr/bin/env python3
"""Pure GC receipt validation and telemetry accounting."""
from __future__ import annotations

from typing import Any

from ael_gc_validation import valid_mechanical_adjudication


def valid_gc_result(
    gc_result: dict[str, Any] | None, result: dict[str, Any],
    subject_digest: str, policy_digest: str,
    mechanical: dict[str, Any] | None = None,
) -> bool:
    return bool(
        gc_result and gc_result.get("decision") == "pass"
        and gc_result.get("role") == "gc-sweeper" and gc_result.get("independent") is True
        and gc_result.get("task_id") == result.get("task_id")
        and gc_result.get("subject_digest") == subject_digest
        and gc_result.get("policy_digest") == policy_digest
        and valid_mechanical_adjudication(gc_result, mechanical)
    )


def telemetry_add_gc(result: dict[str, Any], *, context_chars: int, duration_ms: int) -> None:
    cost = result["cost"]["harness"]
    cost["agent_calls"] = int(cost.get("agent_calls") or 0) + 1
    cost["context_chars"] = int(cost.get("context_chars") or 0) + context_chars
    cost["gate_duration_ms"] = int(cost.get("gate_duration_ms") or 0) + duration_ms
