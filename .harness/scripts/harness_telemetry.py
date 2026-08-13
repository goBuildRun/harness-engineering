#!/usr/bin/env python3
"""Apply trusted-by-subject Agent/provider usage receipts to task cost."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


USAGE_FIELDS = ("input_tokens", "output_tokens", "context_chars", "agent_calls")
SOURCE_TEXT_FIELDS = ("kind", "session_id", "originator", "rollout_sha256", "usage_event_at")
SOURCE_INT_FIELDS = ("rollout_size_bytes", "cached_input_tokens", "reasoning_output_tokens", "total_tokens")


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def apply_usage_receipt(result: dict[str, Any], *, task_id: str,
                        subject_digest: str, policy_digest: str,
                        path: str = "") -> bool:
    raw = path or os.environ.get("HARNESS_USAGE_RECEIPT", "")
    if not raw:
        return False
    try:
        receipt = json.loads(Path(raw).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["blockers"] = sorted(set(result.get("blockers", [])) | {"USAGE_RECEIPT_INVALID"})
        return False
    expected = {
        "task_id": task_id,
        "subject_digest": subject_digest,
        "policy_digest": policy_digest,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        result["blockers"] = sorted(
            set(result.get("blockers", [])) | {"USAGE_RECEIPT_BINDING_MISMATCH"}
        )
        return False
    provider = str(receipt.get("provider") or "").strip()
    model = str(receipt.get("model") or "").strip()
    if not provider or not model:
        result["blockers"] = sorted(
            set(result.get("blockers", [])) | {"USAGE_RECEIPT_SOURCE_MISSING"}
        )
        return False
    complete = True
    for group in ("implementation", "harness"):
        values = receipt.get(group) or {}
        target = result["cost"][group]
        for field in USAGE_FIELDS:
            value = values.get(field, "unknown")
            if _non_negative_int(value):
                target[field] = value
            else:
                complete = False
    result["cost"]["telemetry_complete"] = complete
    result["cost"]["receipt"] = {
        "provider": provider,
        "model": model,
        "task_id": task_id,
        "subject_digest": subject_digest,
        "policy_digest": policy_digest,
    }
    source = receipt.get("source")
    if isinstance(source, dict):
        sanitized = {
            key: str(source[key]).strip()
            for key in SOURCE_TEXT_FIELDS
            if str(source.get(key) or "").strip()
        }
        sanitized.update({
            key: source[key]
            for key in SOURCE_INT_FIELDS
            if _non_negative_int(source.get(key))
        })
        digest = sanitized.get("rollout_sha256", "")
        if digest and (len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower())):
            sanitized.pop("rollout_sha256", None)
        if sanitized:
            result["cost"]["receipt"]["source"] = sanitized
    return True


def apply_gc_telemetry(result: dict[str, Any], gc_result: dict[str, Any] | None) -> bool:
    telemetry = gc_result.get("telemetry") if isinstance(gc_result, dict) else None
    fields = ("agent_calls", "context_chars", "duration_ms")
    provider = str(telemetry.get("provider") or "").strip() if isinstance(telemetry, dict) else ""
    model = str(telemetry.get("model") or "").strip() if isinstance(telemetry, dict) else ""
    if (not isinstance(telemetry, dict) or not provider or not model
            or not all(_non_negative_int(telemetry.get(key)) for key in fields)):
        result["blockers"] = sorted(set(result.get("blockers", [])) | {"GC_TELEMETRY_INVALID"})
        return False
    cost = result["cost"]["harness"]
    cost["agent_calls"] += telemetry["agent_calls"]
    cost["context_chars"] += telemetry["context_chars"]
    cost["gate_duration_ms"] += telemetry["duration_ms"]
    result.setdefault("checks", {}).setdefault("code_health", {}).setdefault(
        "agent", {"provider": provider, "model": model}
    )
    return True


def enforce_budget(result: dict[str, Any]) -> None:
    limits = {
        "input_tokens": os.environ.get("HARNESS_BUDGET_INPUT_TOKENS", ""),
        "output_tokens": os.environ.get("HARNESS_BUDGET_OUTPUT_TOKENS", ""),
        "agent_calls": os.environ.get("HARNESS_BUDGET_AGENT_CALLS", ""),
    }
    exceeded = []
    for field, raw_limit in limits.items():
        if not raw_limit.isdigit():
            continue
        values = [result["cost"][group].get(field) for group in ("implementation", "harness")]
        if all(_non_negative_int(value) for value in values) and sum(values) > int(raw_limit):
            exceeded.append(field)
    if exceeded:
        result["blockers"] = sorted(
            set(result.get("blockers", [])) | {"BUDGET_APPROVAL_REQUIRED"}
        )
        result["cost"]["budget_exceeded"] = exceeded
