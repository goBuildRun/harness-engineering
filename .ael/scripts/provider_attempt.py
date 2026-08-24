#!/usr/bin/env python3
"""One-shot, budget-bound Provider execution after offline preflight."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from ael_candidate import bound_candidate
from ael_cycle_stage_commands import load_candidate_snapshot, validate_current_release_evidence
from ael_runtime import (
    canonical_digest, load_result, now, result_path, task_operation_lock,
)
from ael_task_resolution import valid_task_id
from process_control import run_process_group
from ael_provider_preflight import (
    RECEIPT_SCHEMA as PREFLIGHT_SCHEMA,
    argv_digest,
    execution_environment_digest,
    validate_receipt as validate_canonical_preflight,
)
from ael_gate_inputs import execution_dependency_digest
from ael_execution_authority import AuthorityTrust
from ael_provider_authority import (
    binding_target as authority_binding_target,
    build_binding as build_authority_binding,
    load_json as load_authority_receipt,
    validate_provider_authority,
    validate_sandbox_authority,
    wait_for_receipt,
)
from ael_provider_postcondition import (
    authorization_digest, provider_readiness as _provider_readiness,
    remaining_provider_seconds as _remaining_provider_seconds,  # noqa: F401 - compatibility export
    validate as validate_postcondition, validate_task_precondition,
)
from provider_attempt_evidence import (
    EVIDENCE_SCHEMA,
    evidence_reason as _evidence_reason,
    load_evidence as _load_evidence,
    replace_state as _replace_state,
    sha256_file as _sha256,
    validate_preflight as _validate_preflight,
    write_claim as _write_claim,
)
from worktree_baseline import changed_since_baseline

SCHEMA = "harness-provider-attempt-v3"
FIELDS = {
    "schema", "decision", "reason", "subject_digest", "provider", "mode",
    "provider_process_attempts", "preflight_receipt_digest", "contract_digest",
    "adapter_digest", "canonical_argv_digest", "command_digest", "evidence_ref",
    "execution_dependency_digest", "execution_environment_digest",
    "evidence_schema", "evidence_digest", "attempt_verifier_digest",
    "duration_ms", "completed_at", "receipt_digest",
}

def run_once(
    state_path: Path, preflight: dict[str, Any], expected_subject: str, provider: str,
    evidence_path: Path, evidence_ref: str, command: list[str], timeout_seconds: float,
    runner: Callable[..., Any] = run_process_group, *, adapter_path: Path | None = None,
    precondition: Callable[[], dict[str, Any]] | None = None,
    postcondition: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provider = provider.strip().lower()
    command = [str(item) for item in command]
    if not provider or not command or timeout_seconds <= 0:
        return {"decision": "block", "reason": "PROVIDER_ATTEMPT_INPUT_INVALID"}
    final_deadline = time.monotonic() + timeout_seconds
    verified = _validate_preflight(preflight, expected_subject, provider)
    if verified["decision"] != "pass":
        return {"decision": "block", "reason": verified["reason"]}
    adapter_digest = _sha256(adapter_path.resolve()) if adapter_path is not None else "absent"
    command_digest = argv_digest(command)
    environment_digest = execution_environment_digest()
    try:
        dependency_digest = (
            execution_dependency_digest(adapter_path.resolve(), command)
            if adapter_path is not None else "absent"
        )
    except (OSError, RuntimeError, ValueError):
        dependency_digest = "absent"
    execution_bound = (
        adapter_digest != "absent"
        and adapter_digest == preflight.get("adapter_digest")
        and command_digest == preflight.get("canonical_argv_digest")
        and dependency_digest == preflight.get("execution_dependency_digest")
    )
    if (
        preflight.get("schema") == PREFLIGHT_SCHEMA
        and environment_digest != preflight.get("execution_environment_digest")
    ):
        return {"decision": "block", "reason": "PROVIDER_EXECUTION_ENVIRONMENT_CHANGED"}
    if not execution_bound:
        return {"decision": "block", "reason": "PROVIDER_EXECUTION_DEPENDENCIES_CHANGED"}
    if final_deadline - time.monotonic() <= 0:
        return {"decision": "block", "reason": "PROVIDER_DEADLINE_EXCEEDED"}
    if precondition is not None:
        try:
            pre_status = precondition()
            if not isinstance(pre_status, dict):
                raise ValueError
        except (OSError, RuntimeError, TypeError, ValueError):
            return {"decision": "block", "reason": "PROVIDER_PRECONDITION_INVALID"}
        if pre_status.get("decision") != "pass":
            return {
                "decision": "block",
                "reason": str(pre_status.get("reason") or "PROVIDER_PRECONDITION_INVALID"),
            }
    if final_deadline - time.monotonic() <= 0:
        return {"decision": "block", "reason": "PROVIDER_DEADLINE_EXCEEDED"}
    evidence_before = _sha256(evidence_path)
    claim = {
        "schema": SCHEMA,
        "state": "claimed",
        "subject_digest": expected_subject,
        "provider": provider,
        "preflight_receipt_digest": preflight["receipt_digest"],
        "contract_digest": preflight["contract_digest"],
        "adapter_digest": adapter_digest,
        "canonical_argv_digest": command_digest,
        "execution_dependency_digest": dependency_digest,
        "execution_environment_digest": environment_digest,
        "claimed_at": now(),
    }
    if not _write_claim(state_path, claim):
        return {"decision": "block", "reason": "PROVIDER_ATTEMPT_LIMIT_EXCEEDED"}
    remaining_timeout = max(0.0, final_deadline - time.monotonic())
    if remaining_timeout <= 0:
        returncode = 124
        duration_ms = 0
    else:
        started = time.monotonic()
        execution_env = os.environ.copy()
        execution_env.pop("AEL_PROVIDER_OFFLINE_TRACE", None)
        execution_env["AEL_PROVIDER_EXECUTION_MODE"] = "real"
        try:
            completed = runner(
                command,
                cwd=adapter_path.resolve().parent if adapter_path is not None else None,
                env=execution_env, timeout=remaining_timeout,
            )
            returncode = int(completed.returncode)
        except subprocess.TimeoutExpired:
            returncode = 124
        except Exception:
            returncode = 125
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
    evidence_digest, evidence_payload = _load_evidence(evidence_path)
    evidence_reason = _evidence_reason(evidence_payload, expected_subject, provider)
    evidence_fresh = evidence_digest != "absent" and evidence_digest != evidence_before
    adapter_unchanged = _sha256(adapter_path.resolve()) == adapter_digest
    try:
        dependencies_unchanged = (
            execution_dependency_digest(adapter_path.resolve(), command) == dependency_digest
        )
    except (OSError, RuntimeError, ValueError):
        dependencies_unchanged = False
    post_status = {"decision": "pass", "reason": "PROVIDER_POSTCONDITION_OK"}
    if postcondition is not None:
        try:
            candidate_status = postcondition()
            if not isinstance(candidate_status, dict):
                raise ValueError
            post_status = candidate_status
        except (OSError, RuntimeError, TypeError, ValueError):
            post_status = {"decision": "block", "reason": "PROVIDER_POSTCONDITION_INVALID"}
    if not adapter_unchanged:
        decision, reason = "block", "PROVIDER_ADAPTER_CHANGED"
    elif not dependencies_unchanged:
        decision, reason = "block", "PROVIDER_EXECUTION_DEPENDENCIES_CHANGED"
    elif returncode == 124:
        decision, reason = "block", "PROVIDER_DEADLINE_EXCEEDED"
    elif returncode != 0:
        decision, reason = "block", "PROVIDER_PROCESS_FAILED"
    elif evidence_digest == "absent":
        decision, reason = "block", "PROVIDER_EVIDENCE_MISSING"
    elif not evidence_fresh:
        decision, reason = "block", "PROVIDER_EVIDENCE_STALE"
    elif evidence_reason != "PROVIDER_EVIDENCE_OK":
        decision, reason = "block", evidence_reason
    elif post_status.get("decision") != "pass":
        decision = "block"
        reason = str(post_status.get("reason") or "PROVIDER_POSTCONDITION_INVALID")
    else:
        decision, reason = "pass", "PROVIDER_ATTEMPT_OK"
    receipt = {
        "schema": SCHEMA,
        "decision": decision,
        "reason": reason,
        "subject_digest": expected_subject,
        "provider": provider,
        "mode": "real",
        "provider_process_attempts": 1,
        "preflight_receipt_digest": preflight["receipt_digest"],
        "contract_digest": preflight["contract_digest"],
        "adapter_digest": adapter_digest,
        "canonical_argv_digest": command_digest,
        "execution_dependency_digest": dependency_digest,
        "execution_environment_digest": environment_digest,
        "command_digest": command_digest,
        "evidence_ref": evidence_ref,
        "evidence_schema": str(evidence_payload.get("schema") or ""),
        "evidence_digest": evidence_digest,
        "attempt_verifier_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "duration_ms": duration_ms,
        "completed_at": now(),
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    _replace_state(state_path, receipt)
    return receipt

def validate_receipt(
    receipt: dict[str, Any], preflight: dict[str, Any], expected_subject: str,
    expected_provider: str, evidence_path: Path | None = None,
) -> dict[str, str]:
    expected_provider = expected_provider.strip().lower()
    if set(receipt) != FIELDS:
        return {"decision": "block", "reason": "PROVIDER_ATTEMPT_RECEIPT_FIELDS_INVALID"}
    if _validate_preflight(preflight, expected_subject, expected_provider)["decision"] != "pass":
        return {"decision": "block", "reason": "PROVIDER_ATTEMPT_PREFLIGHT_INVALID"}
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    evidence_digest, evidence_payload = (
        _load_evidence(evidence_path) if evidence_path is not None else ("absent", {})
    )
    evidence_valid = (
        evidence_path is not None
        and evidence_digest == receipt.get("evidence_digest")
        and _evidence_reason(evidence_payload, expected_subject, expected_provider)
        == "PROVIDER_EVIDENCE_OK"
    )
    valid = (
        receipt.get("receipt_digest") == canonical_digest(unsigned)
        and receipt.get("decision") == "pass"
        and receipt.get("subject_digest") == expected_subject
        and receipt.get("provider") == expected_provider
        and receipt.get("mode") == "real"
        and receipt.get("provider_process_attempts") == 1
        and receipt.get("preflight_receipt_digest") == preflight.get("receipt_digest")
        and receipt.get("contract_digest") == preflight.get("contract_digest")
        and receipt.get("adapter_digest") == preflight.get("adapter_digest")
        and receipt.get("canonical_argv_digest") == preflight.get("canonical_argv_digest")
        and receipt.get("command_digest") == preflight.get("canonical_argv_digest")
        and receipt.get("execution_dependency_digest")
        == preflight.get("execution_dependency_digest")
        and (
            preflight.get("schema") != PREFLIGHT_SCHEMA
            or receipt.get("execution_environment_digest")
            == preflight.get("execution_environment_digest")
        )
        and receipt.get("attempt_verifier_digest")
        == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        and isinstance(receipt.get("evidence_ref"), str)
        and bool(receipt["evidence_ref"].strip())
        and receipt.get("evidence_schema") == EVIDENCE_SCHEMA
        and evidence_valid
    )
    return {
        "decision": "pass" if valid else "block",
        "reason": "PROVIDER_ATTEMPT_RECEIPT_OK" if valid else "PROVIDER_ATTEMPT_RECEIPT_INVALID",
    }

def attempt_for_task(
    product: Path, task_id: str, *, preflight_path: Path, expected_subject: str,
    provider: str, adapter_path: Path, evidence_ref: str, command: list[str],
    timeout_seconds: float, runner: Callable[..., Any] = run_process_group,
    sandbox_authority_path: Path | None = None,
    provider_authority_path: Path | None = None,
    authority_binding_path: Path | None = None,
    sandbox_trust: AuthorityTrust | None = None,
    provider_trust: AuthorityTrust | None = None,
    authority_required: bool = False,
) -> dict[str, Any]:
    product = product.resolve()
    if not valid_task_id(task_id):
        return {"decision": "block", "reason": "TASK_ID_INVALID"}
    task_path = result_path(product, task_id)
    if not task_path.is_file():
        return {"decision": "block", "reason": "TASK_NOT_FOUND"}
    authority_target = authority_binding_target(product, task_path, authority_binding_path)
    if authority_required and authority_target is None:
        return {"decision": "block", "reason": "PROVIDER_AUTHORITY_BINDING_PATH_INVALID"}
    try:
        with task_operation_lock(task_path):
            result = load_result(task_path)
            baseline = task_path.parent / "worktree_baseline.json"
            changed = changed_since_baseline(product, baseline)
            snapshot = load_candidate_snapshot(task_path)
            candidate = (
                bound_candidate(product, snapshot, result.get("candidate"), changed)
                if snapshot else {"decision": "block", "reason": "CANDIDATE_NOT_FROZEN"}
            )
            if candidate["decision"] == "block":
                return candidate
            subject = str(candidate["candidate_digest"])
            if subject != expected_subject:
                return {"decision": "block", "reason": "PROVIDER_ATTEMPT_SUBJECT_MISMATCH"}
            preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            sandbox_authority: dict[str, Any] = {}
            if authority_required:
                if sandbox_authority_path is None or sandbox_trust is None:
                    return {"decision": "block", "reason": "PROVIDER_SANDBOX_AUTHORITY_REQUIRED"}
                canonical_status = validate_canonical_preflight(
                    preflight, subject, provider.strip().lower(),
                )
                if canonical_status["decision"] != "pass":
                    return canonical_status
                sandbox_authority = load_authority_receipt(sandbox_authority_path)
                sandbox_status = validate_sandbox_authority(
                    sandbox_authority, preflight, sandbox_trust,
                )
                if sandbox_status["decision"] != "pass":
                    return sandbox_status
            evidence = (product / evidence_ref).resolve()
            evidence.relative_to(product)
            result_digest = authorization_digest(result)
            readiness, remaining = _provider_readiness(result, timeout_seconds)
            if readiness["decision"] == "block":
                return readiness
            evidence_status = validate_current_release_evidence(product, task_id, result)
            if evidence_status["decision"] == "block":
                return evidence_status
            readiness, remaining = _provider_readiness(result, timeout_seconds)
            if readiness["decision"] == "block":
                return readiness

            authority_deadline = time.monotonic() + remaining
            attempt = run_once(
                task_path.parent / "provider-attempt.json", preflight, subject, provider,
                evidence, evidence_ref, command, remaining, runner,
                adapter_path=adapter_path,
                precondition=lambda: validate_task_precondition(
                    product, task_path, baseline, task_id, subject, result_digest,
                ),
                postcondition=lambda: validate_postcondition(
                    product, task_path, baseline, subject,
                    expected_result_digest=result_digest,
                ),
            )
            if not authority_required or attempt.get("decision") != "pass":
                return attempt
            if provider_authority_path is None or provider_trust is None:
                return {"decision": "block", "reason": "PROVIDER_RESPONSE_AUTHORITY_REQUIRED"}
            provider_authority = wait_for_receipt(
                provider_authority_path, deadline=authority_deadline,
                validator=lambda receipt: validate_provider_authority(
                    receipt, preflight, attempt, provider_trust,
                ).get("decision") == "pass",
            )
            binding = build_authority_binding(
                preflight=preflight,
                attempt=attempt,
                sandbox_authority=sandbox_authority,
                provider_authority=provider_authority,
                sandbox_trust=sandbox_trust,
                provider_trust=provider_trust,
            )
            if binding.get("decision") != "pass":
                return binding
            if authority_target is None:
                return {"decision": "block", "reason": "PROVIDER_AUTHORITY_BINDING_PATH_INVALID"}
            _replace_state(authority_target, binding)
            return {
                "decision": "pass",
                "reason": "PROVIDER_ATTEMPT_AUTHORIZED",
                "attempt": attempt,
                "authority_binding": binding,
            }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"decision": "block", "reason": str(exc) or "PROVIDER_ATTEMPT_INVALID"}

def main() -> int:
    from provider_attempt_cli import run_cli

    return run_cli(attempt_for_task)

if __name__ == "__main__":
    raise SystemExit(main())
