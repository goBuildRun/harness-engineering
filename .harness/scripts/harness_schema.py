#!/usr/bin/env python3
"""Mechanical schema validation for the shared task result protocol."""
from __future__ import annotations

from typing import Any


RESULT_STATES = {"active", "blocked", "validated"}
DECISIONS = {"pass", "block"}
TIERS = {"lite", "standard", "strict"}
ASSURANCE_LEVELS = {"local", "guarded", "enforced"}
SOURCES = {"executed", "cache"}
INVARIANTS = {"task_identity", "scope", "risk_validation", "final_result"}
COST_FIELDS = {"input_tokens", "output_tokens", "context_chars", "agent_calls"}


def validate_result(data: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if data.get("schema_version") != 1:
        issues.append("schema_version")
    if not isinstance(data.get("task_id"), str) or not data.get("task_id"):
        issues.append("task_id")
    if data.get("state") not in RESULT_STATES:
        issues.append("state")
    if data.get("enforcement") not in {"shadow", "enforced"}:
        issues.append("enforcement")
    assurance = data.get("assurance") or {}
    if assurance.get("level") not in ASSURANCE_LEVELS:
        issues.append("assurance.level")
    if assurance.get("task_execution") not in {"complete", "incomplete"}:
        issues.append("assurance.task_execution")
    if assurance.get("acceptance_authority") not in {
        "worktree", "git-guards+ci", "protected-authority",
    }:
        issues.append("assurance.acceptance_authority")
    if not isinstance(assurance.get("bypassable"), bool):
        issues.append("assurance.bypassable")
    if not isinstance(assurance.get("verified_at"), str):
        issues.append("assurance.verified_at")
    if not isinstance(assurance.get("blockers"), list):
        issues.append("assurance.blockers")
    if assurance.get("level") == "enforced" and data.get("enforcement") != "enforced":
        issues.append("assurance.enforcement_mismatch")
    if data.get("enforcement") == "enforced" and assurance.get("level") != "enforced":
        issues.append("enforcement.assurance_mismatch")
    if data.get("decision") not in DECISIONS:
        issues.append("decision")
    tier = data.get("tier") or {}
    if tier.get("initial") not in TIERS or tier.get("effective") not in TIERS:
        issues.append("tier")
    if not isinstance(data.get("baseline"), dict) or not isinstance(data.get("subject"), dict):
        issues.append("subject_or_baseline")
    invariants = data.get("invariants") or {}
    if set(invariants) != INVARIANTS or any(
        value not in {"pending", "pass", "block"} for value in invariants.values()
    ):
        issues.append("invariants")
    for name, check in (data.get("checks") or {}).items():
        if not isinstance(check, dict) or check.get("decision") not in DECISIONS:
            issues.append(f"checks.{name}.decision")
            continue
        for field in ("fingerprint", "subject_digest"):
            if not isinstance(check.get(field), str):
                issues.append(f"checks.{name}.{field}")
        if check.get("source") not in SOURCES:
            issues.append(f"checks.{name}.source")
    cost = data.get("cost") or {}
    for group in ("implementation", "harness"):
        values = cost.get(group) or {}
        fields = COST_FIELDS | ({"gate_duration_ms", "reruns"} if group == "harness" else set())
        for field in fields:
            value = values.get(field)
            if value != "unknown" and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                issues.append(f"cost.{group}.{field}")
        if group == "harness" and "cache_hits" in values:
            cache_hits = values["cache_hits"]
            if not isinstance(cache_hits, int) or isinstance(cache_hits, bool) or cache_hits < 0:
                issues.append("cost.harness.cache_hits")
    if cost.get("telemetry_complete") not in {True, False}:
        issues.append("cost.telemetry_complete")
    if cost.get("telemetry_complete") and _contains_unknown(cost):
        issues.append("cost.telemetry_complete_unknown")
    receipt = cost.get("receipt")
    if receipt is not None:
        if not isinstance(receipt, dict):
            issues.append("cost.receipt")
        else:
            for field in ("provider", "model", "task_id", "subject_digest", "policy_digest"):
                if not isinstance(receipt.get(field), str) or not receipt[field].strip():
                    issues.append(f"cost.receipt.{field}")
            bindings = {
                "task_id": data.get("task_id"),
                "subject_digest": (data.get("subject") or {}).get("digest"),
                "policy_digest": data.get("policy_digest"),
            }
            for field, expected in bindings.items():
                if receipt.get(field) != expected:
                    issues.append(f"cost.receipt.{field}_mismatch")
    return issues


def assert_result(data: dict[str, Any]) -> None:
    issues = validate_result(data)
    if issues:
        raise ValueError(f"RESULT_SCHEMA_INVALID: {','.join(sorted(set(issues)))}")


def _contains_unknown(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_unknown(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_unknown(item) for item in value)
    return value == "unknown"
