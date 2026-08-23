#!/usr/bin/env python3
"""Bind sandbox and Provider-response authorities to one real Provider attempt."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from harness_execution_authority import AuthorityTrust, validate_receipt
from harness_runtime import canonical_digest, now


SCHEMA = "harness-provider-authority-binding-v1"
FIELDS = {
    "schema", "decision", "reason", "subject_digest", "provider",
    "preflight_receipt_digest", "attempt_receipt_digest",
    "sandbox_authority", "provider_authority", "completed_at", "receipt_digest",
}
SANDBOX_CLAIMS = {"network": "none", "workspace": "read-only"}


def binding_target(product: Path, task_path: Path, requested: Path | None) -> Path | None:
    runtime = task_path.parent.resolve()
    target = requested or runtime / "provider-authority-binding.json"
    if not target.is_absolute():
        target = product / target
    try:
        target = target.resolve()
    except OSError:
        return None
    return target if target.parent == runtime else None


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PROVIDER_AUTHORITY_RECEIPT_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError("PROVIDER_AUTHORITY_RECEIPT_INVALID")
    return value


def wait_for_receipt(
    path: Path, *, deadline: float,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    poll_seconds: float = 0.05,
    validator: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Wait for one atomically published authority receipt within the caller's budget."""
    while True:
        try:
            receipt = load_json(path)
            if validator is None or validator(receipt):
                return receipt
        except ValueError:
            pass
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError("PROVIDER_RESPONSE_AUTHORITY_TIMEOUT")
        sleeper(min(poll_seconds, remaining))


def sandbox_input_digest(preflight: dict[str, Any]) -> str:
    return canonical_digest({
        "subject_digest": preflight.get("subject_digest"),
        "provider": preflight.get("provider"),
        "contract_digest": preflight.get("contract_digest"),
        "adapter_digest": preflight.get("adapter_digest"),
        "canonical_argv_digest": preflight.get("canonical_argv_digest"),
        "execution_dependency_digest": preflight.get("execution_dependency_digest"),
        "execution_environment_digest": preflight.get("execution_environment_digest"),
    })


def provider_input_digest(preflight: dict[str, Any], attempt: dict[str, Any]) -> str:
    return canonical_digest({
        "subject_digest": attempt.get("subject_digest"),
        "provider": attempt.get("provider"),
        "preflight_receipt_digest": preflight.get("receipt_digest"),
        "attempt_receipt_digest": attempt.get("receipt_digest"),
        "contract_digest": attempt.get("contract_digest"),
        "adapter_digest": attempt.get("adapter_digest"),
        "canonical_argv_digest": attempt.get("canonical_argv_digest"),
        "execution_dependency_digest": attempt.get("execution_dependency_digest"),
        "execution_environment_digest": attempt.get("execution_environment_digest"),
    })


def validate_sandbox_authority(
    receipt: dict[str, Any], preflight: dict[str, Any], trust: AuthorityTrust,
) -> dict[str, str]:
    return validate_receipt(
        receipt,
        trust=trust,
        authority="network-sandbox",
        action="provider-preflight",
        subject_digest=str(preflight.get("subject_digest") or ""),
        provider=str(preflight.get("provider") or ""),
        input_digest=sandbox_input_digest(preflight),
        output_digest=str(preflight.get("trace_digest") or ""),
        claims=SANDBOX_CLAIMS,
    )


def _provider_claims(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_ref": str(attempt.get("evidence_ref") or ""),
        "evidence_schema": str(attempt.get("evidence_schema") or ""),
        "response_authoritative": True,
    }


def validate_provider_authority(
    receipt: dict[str, Any], preflight: dict[str, Any], attempt: dict[str, Any],
    trust: AuthorityTrust,
) -> dict[str, str]:
    return validate_receipt(
        receipt,
        trust=trust,
        authority="provider-response",
        action="provider-execution",
        subject_digest=str(attempt.get("subject_digest") or ""),
        provider=str(attempt.get("provider") or ""),
        input_digest=provider_input_digest(preflight, attempt),
        output_digest=str(attempt.get("evidence_digest") or ""),
        claims=_provider_claims(attempt),
    )


def build_binding(
    *, preflight: dict[str, Any], attempt: dict[str, Any],
    sandbox_authority: dict[str, Any], provider_authority: dict[str, Any],
    sandbox_trust: AuthorityTrust, provider_trust: AuthorityTrust,
) -> dict[str, Any]:
    sandbox = validate_sandbox_authority(sandbox_authority, preflight, sandbox_trust)
    if sandbox["decision"] != "pass":
        return {"decision": "block", "reason": sandbox["reason"]}
    if attempt.get("decision") != "pass":
        return {"decision": "block", "reason": "PROVIDER_AUTHORITY_ATTEMPT_NOT_PASS"}
    response = validate_provider_authority(
        provider_authority, preflight, attempt, provider_trust,
    )
    if response["decision"] != "pass":
        return {"decision": "block", "reason": response["reason"]}
    binding = {
        "schema": SCHEMA,
        "decision": "pass",
        "reason": "PROVIDER_AUTHORITIES_VALID",
        "subject_digest": str(attempt.get("subject_digest") or ""),
        "provider": str(attempt.get("provider") or ""),
        "preflight_receipt_digest": str(preflight.get("receipt_digest") or ""),
        "attempt_receipt_digest": str(attempt.get("receipt_digest") or ""),
        "sandbox_authority": sandbox_authority,
        "provider_authority": provider_authority,
        "completed_at": now(),
    }
    binding["receipt_digest"] = canonical_digest(binding)
    return binding


def validate_binding(
    binding: Any, *, preflight: dict[str, Any], attempt: dict[str, Any],
    sandbox_trust: AuthorityTrust, provider_trust: AuthorityTrust,
) -> dict[str, str]:
    if not isinstance(binding, dict) or set(binding) != FIELDS:
        return {"decision": "block", "reason": "PROVIDER_AUTHORITY_BINDING_FIELDS_INVALID"}
    unsigned = {key: value for key, value in binding.items() if key != "receipt_digest"}
    if binding.get("receipt_digest") != canonical_digest(unsigned):
        return {"decision": "block", "reason": "PROVIDER_AUTHORITY_BINDING_DIGEST_INVALID"}
    rebuilt = build_binding(
        preflight=preflight,
        attempt=attempt,
        sandbox_authority=binding.get("sandbox_authority") or {},
        provider_authority=binding.get("provider_authority") or {},
        sandbox_trust=sandbox_trust,
        provider_trust=provider_trust,
    )
    if rebuilt.get("decision") != "pass":
        return {"decision": "block", "reason": str(rebuilt.get("reason") or "PROVIDER_AUTHORITY_INVALID")}
    compared = FIELDS - {"completed_at", "receipt_digest"}
    if any(binding.get(field) != rebuilt.get(field) for field in compared):
        return {"decision": "block", "reason": "PROVIDER_AUTHORITY_BINDING_INVALID"}
    return {"decision": "pass", "reason": "PROVIDER_AUTHORITY_BINDING_VALID"}
