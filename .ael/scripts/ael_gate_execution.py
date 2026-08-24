#!/usr/bin/env python3
"""Public gate execution API with bounded parallel scheduling and cache reuse."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ael_cache import CACHEABLE_GATES, reuse_check
from ael_gate_inputs import SHELL_TOOL_REF, gate_input_digest
from ael_gate_manifest import (
    MAX_CANDIDATE_BYTES,
    MAX_CANDIDATE_FILES,
    MAX_SCAN_ENTRIES,
    READ_CHUNK_BYTES,
    CandidateManifest,
    GateInputError,
)
from ael_runtime import canonical_digest, now
from ael_timing import append_event


PARALLEL_READ_ONLY_GATES = {
    "harness", "structure", "diff_integrity", "plan_sync", "dag_sync",
    "qa_evidence", "knowledge", "growth_review", "growth_freshness", "growth_release",
}
NEVER_CACHE_GATES = {
    "qa_evidence", "growth_review", "growth_freshness", "growth_release", "quality_lint",
    "quality_test", "browser_qa", "strict_evidence", "provider", "deployment",
    "rollback", "gc_agent",
}
DEFAULT_TIMEOUTS = {
    "quality_lint": 180,
    "quality_test": 600,
    "browser_qa": 300,
}


@dataclass(frozen=True)
class GateSpec:
    name: str
    command: tuple[str, ...]
    input_digest: str
    fingerprint: str
    timeout_seconds: float
    budget_ms: int
    parallel_safe: bool
    cacheable: bool


def build_spec(
    name: str,
    command: list[str],
    *,
    input_digest: str,
    tier: str,
    configured_timeout: int,
    remaining_budget_ms: int | None,
    cache_safe: bool = True,
) -> GateSpec:
    gate_timeout = float(DEFAULT_TIMEOUTS.get(name, configured_timeout))
    if configured_timeout != 120:
        gate_timeout = configured_timeout
    if remaining_budget_ms is not None:
        gate_timeout = min(gate_timeout, max(0.001, remaining_budget_ms / 1000))
    fingerprint = canonical_digest({
        "schema": 3, "gate": name, "input_digest": input_digest,
        "tier": tier, "command_kind": Path(command[0]).name if command else "read",
    })
    return GateSpec(
        name=name,
        command=tuple(command),
        input_digest=input_digest,
        fingerprint=fingerprint,
        timeout_seconds=gate_timeout,
        budget_ms=max(1, int(gate_timeout * 1000)),
        parallel_safe=name in PARALLEL_READ_ONLY_GATES,
        cacheable=(
            cache_safe and name in CACHEABLE_GATES and name not in NEVER_CACHE_GATES
        ),
    )


def _executed_check(
    spec: GateSpec, payload: dict[str, Any], *, returncode: int, duration_ms: int,
    subject_digest: str, policy_digest: str, started_at: str,
) -> dict[str, Any]:
    reason = str(payload.get("reason") or "")
    timeout_kind = "gate" if returncode == 124 else "none"
    remediation = ""
    if spec.name == "knowledge" and payload.get("decision") == "block":
        remediation = "ael_knowledge.sh sync-planning"
    elif spec.name == "growth_freshness" and reason.startswith("GROWTH_"):
        remediation = "ael_growth.sh scan"
    return {
        "decision": payload["decision"],
        "source": "executed",
        "subject_digest": subject_digest,
        "policy_digest": policy_digest,
        "input_digest": spec.input_digest,
        "fingerprint": spec.fingerprint,
        "started_at": started_at,
        "completed_at": now(),
        "duration_ms": duration_ms,
        "tool_wait_ms": duration_ms,
        "attempt": 1,
        "retry_limit": 1,
        "budget_ms": spec.budget_ms,
        "timeout_kind": timeout_kind,
        "reason": reason,
        **({"next_action": remediation} if remediation else {}),
    }


def execute_specs(
    specs: list[GateSpec],
    *,
    runner: Callable[[list[str], str, float], tuple[dict[str, Any], int]],
    previous_checks: dict[str, dict[str, Any]],
    subject_digest: str,
    policy_digest: str,
    ledger: Path | None = None,
    task_id: str = "",
    work_item_id: str = "",
    remaining_budget_ms: int | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    candidates_manifest: CandidateManifest | None = None,
) -> tuple[dict[str, dict[str, Any]], int]:
    checks: dict[str, dict[str, Any]] = {}
    pending: list[GateSpec] = []
    cache_hits = 0
    if deadline is None and remaining_budget_ms is not None:
        deadline = clock() + max(0, remaining_budget_ms) / 1000

    def integrity_block(reason: str) -> tuple[dict[str, dict[str, Any]], int]:
        completed = now()
        return ({
            spec.name: {
                "decision": "block", "source": "not_executed",
                "subject_digest": subject_digest, "policy_digest": policy_digest,
                "input_digest": spec.input_digest, "fingerprint": spec.fingerprint,
                "started_at": completed, "completed_at": completed,
                "duration_ms": 0, "tool_wait_ms": 0, "attempt": 0,
                "retry_limit": 1, "budget_ms": spec.budget_ms,
                "timeout_kind": "stage" if reason == "STAGE_BUDGET_EXCEEDED" else "input",
                "reason": reason,
            } for spec in specs
        }, 0)

    if candidates_manifest is not None:
        try:
            candidates_manifest.verify_fresh()
        except GateInputError as exc:
            return integrity_block(exc.reason)
    for spec in specs:
        cached = reuse_check(
            previous_checks.get(spec.name), gate=spec.name, fingerprint=spec.fingerprint,
            subject_digest=subject_digest, policy_digest=policy_digest,
            input_digest=spec.input_digest,
        ) if (
            spec.cacheable and (deadline is None or clock() < deadline)
        ) else None
        if cached:
            cached.update({"duration_ms": 0, "tool_wait_ms": 0, "cache_hit": True})
            checks[spec.name] = cached
            cache_hits += 1
        else:
            pending.append(spec)

    def execute(spec: GateSpec) -> tuple[str, dict[str, Any]]:
        started_at = now()
        started_epoch_ms = int(time.time() * 1000)
        span_id = f"gate-{spec.name}-{started_epoch_ms}"
        remaining = spec.timeout_seconds
        if deadline is not None:
            remaining = min(remaining, max(0.0, deadline - clock()))
        if remaining <= 0:
            return spec.name, {
                "decision": "block", "source": "not_executed",
                "subject_digest": subject_digest, "policy_digest": policy_digest,
                "input_digest": spec.input_digest, "fingerprint": spec.fingerprint,
                "started_at": started_at, "completed_at": now(),
                "duration_ms": 0, "tool_wait_ms": 0, "attempt": 0,
                "retry_limit": 1, "budget_ms": 0, "timeout_kind": "stage",
                "reason": "STAGE_BUDGET_EXCEEDED",
            }
        if ledger is not None:
            append_event(ledger, {
                "task_id": task_id, "work_item_id": work_item_id,
                "stage": f"gate:{spec.name}", "event": "start", "span_id": span_id,
                "parent_span_id": "finalize", "attempt": 1, "epoch_ms": started_epoch_ms,
                "input_digest": spec.input_digest, "budget_ms": spec.budget_ms,
                "reason": "GATE_STARTED",
            })
        started = clock()
        payload, returncode = runner(list(spec.command), spec.name, remaining)
        duration_ms = max(0, int((clock() - started) * 1000))
        check = _executed_check(
            spec, payload, returncode=returncode, duration_ms=duration_ms,
            subject_digest=subject_digest, policy_digest=policy_digest,
            started_at=started_at,
        )
        if deadline is not None and clock() >= deadline:
            check.update({
                "decision": "block", "reason": "STAGE_BUDGET_EXCEEDED",
                "timeout_kind": "stage",
            })
        if ledger is not None:
            append_event(ledger, {
                "task_id": task_id, "work_item_id": work_item_id,
                "stage": f"gate:{spec.name}", "event": "end", "span_id": span_id,
                "attempt": 1, "epoch_ms": int(time.time() * 1000),
                "wall_ms": duration_ms, "tool_wait_ms": duration_ms,
                "decision": check["decision"],
                "reason": (
                    "STAGE_BUDGET_EXCEEDED" if check["reason"] == "STAGE_BUDGET_EXCEEDED"
                    else "GATE_TIMEOUT" if returncode == 124 else "GATE_FINISHED"
                ),
                "input_digest": spec.input_digest, "cache_hit": False,
            })
        return spec.name, check

    parallel = [spec for spec in pending if spec.parallel_safe]
    serial = [spec for spec in pending if not spec.parallel_safe]
    if parallel:
        with ThreadPoolExecutor(max_workers=min(8, len(parallel))) as pool:
            futures = [pool.submit(execute, spec) for spec in parallel]
            for future in as_completed(futures):
                name, check = future.result()
                checks[name] = check
    for spec in serial:
        name, check = execute(spec)
        checks[name] = check
    if candidates_manifest is not None:
        try:
            candidates_manifest.verify_fresh()
        except GateInputError as exc:
            completed = now()
            for spec in specs:
                check = checks[spec.name]
                check.update({
                    "decision": "block", "completed_at": completed,
                    "timeout_kind": (
                        "stage" if exc.reason == "STAGE_BUDGET_EXCEEDED" else "input"
                    ),
                    "reason": exc.reason, "cache_hit": False,
                })
            cache_hits = 0
    ordered = {spec.name: checks[spec.name] for spec in specs if spec.name in checks}
    return ordered, cache_hits
