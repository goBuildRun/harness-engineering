#!/usr/bin/env python3
"""One-shot, budget-bound Provider execution after offline preflight."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from harness_candidate import bound_candidate
from harness_cycle_stage_commands import load_candidate_snapshot, validate_current_release_evidence
from harness_output import dump_json
from harness_runtime import (
    canonical_digest, load_result, now, result_path, task_operation_lock,
)
from harness_task_resolution import valid_task_id
from process_control import run_process_group
from harness_provider_preflight import (
    RECEIPT_SCHEMA as PREFLIGHT_SCHEMA,
    argv_digest,
    execution_environment_digest,
    validate_receipt as validate_harness_preflight,
)
from harness_gate_inputs import execution_dependency_digest
from harness_provider_postcondition import (
    authorization_digest, provider_readiness as _provider_readiness,
    remaining_provider_seconds as _remaining_provider_seconds,
    validate as validate_postcondition, validate_task_precondition,
)
from worktree_baseline import changed_since_baseline

SCHEMA = "harness-provider-attempt-v3"
EVIDENCE_SCHEMA = "harness-provider-evidence-v1"
FIELDS = {
    "schema", "decision", "reason", "subject_digest", "provider", "mode",
    "provider_process_attempts", "preflight_receipt_digest", "contract_digest",
    "adapter_digest", "canonical_argv_digest", "command_digest", "evidence_ref",
    "execution_dependency_digest", "execution_environment_digest",
    "evidence_schema", "evidence_digest", "attempt_verifier_digest",
    "duration_ms", "completed_at", "receipt_digest",
}

def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"

def _load_evidence(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "absent" if not path.is_file() else _sha256(path), {}
    return hashlib.sha256(raw).hexdigest(), value if isinstance(value, dict) else {}

def _evidence_reason(payload: dict[str, Any], subject: str, provider: str) -> str:
    if payload.get("schema") != EVIDENCE_SCHEMA:
        return "PROVIDER_EVIDENCE_SCHEMA_INVALID"
    if payload.get("decision") != "pass":
        return "PROVIDER_EVIDENCE_DECISION_INVALID"
    if payload.get("subject_digest") != subject:
        return "PROVIDER_EVIDENCE_SUBJECT_MISMATCH"
    if payload.get("provider") != provider:
        return "PROVIDER_EVIDENCE_PROVIDER_MISMATCH"
    return "PROVIDER_EVIDENCE_OK"

def _validate_preflight(
    receipt: dict[str, Any], expected_subject: str, expected_provider: str,
) -> dict[str, str]:
    primary = validate_harness_preflight(receipt, expected_subject, expected_provider)
    if primary["decision"] == "pass" or receipt.get("schema") == PREFLIGHT_SCHEMA:
        return primary
    try:
        from provider_verifier_preflight import validate_receipt as validate_compatibility

        compatibility = validate_compatibility(
            receipt, expected_subject, expected_provider,
        )
    except (ImportError, TypeError, ValueError):
        return primary
    return compatibility if compatibility.get("decision") == "pass" else primary

def _write_claim(path: Path, payload: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return True

def _replace_state(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

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
        execution_env.pop("HARNESS_PROVIDER_OFFLINE_TRACE", None)
        execution_env["HARNESS_PROVIDER_EXECUTION_MODE"] = "real"
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
) -> dict[str, Any]:
    product = product.resolve()
    if not valid_task_id(task_id):
        return {"decision": "block", "reason": "TASK_ID_INVALID"}
    task_path = result_path(product, task_id)
    if not task_path.is_file():
        return {"decision": "block", "reason": "TASK_NOT_FOUND"}
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

            return run_once(
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
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"decision": "block", "reason": str(exc) or "PROVIDER_ATTEMPT_INVALID"}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--expected-subject", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--evidence-ref", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    product = Path(args.product_root).resolve()
    outcome = attempt_for_task(
        product, args.task_id, preflight_path=Path(args.preflight),
        expected_subject=args.expected_subject, provider=args.provider,
        adapter_path=Path(args.adapter), evidence_ref=args.evidence_ref,
        command=command, timeout_seconds=args.timeout_seconds,
    )
    dump_json(outcome)
    return 0 if outcome.get("decision") == "pass" else 1

if __name__ == "__main__":
    raise SystemExit(main())
