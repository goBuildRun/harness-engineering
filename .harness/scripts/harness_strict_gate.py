#!/usr/bin/env python3
"""Strict gate adapter with component and Provider digest-chain binding."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from harness_provider_preflight import validate_receipt as validate_provider_preflight
from provider_attempt import validate_receipt as validate_provider_attempt
from harness_execution_authority import trust_from_installation
from harness_provider_authority import validate_binding as validate_provider_authorities


COMPONENT_SCHEMA = "harness-strict-evidence-component-v1"
COMPONENT_FIELDS = {
    "schema", "decision", "subject_digest", "authority", "evidence_ref",
    "evidence_digest",
}
COMPONENT_AUTHORITIES = {
    "browser_qa": "browser-qa",
    "deployment": "deployment-controller",
    "rollback": "rollback-verifier",
}


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


def _component_valid(
    value: Any, *, name: str, subject_digest: str, product_root: Path | None,
) -> bool:
    if not isinstance(value, dict) or set(value) != COMPONENT_FIELDS or product_root is None:
        return False
    evidence_ref = value.get("evidence_ref")
    if not isinstance(evidence_ref, str) or not evidence_ref or Path(evidence_ref).is_absolute():
        return False
    try:
        root = product_root.resolve()
        evidence = (root / evidence_ref).resolve()
        evidence.relative_to(root)
    except (OSError, ValueError):
        return False
    return (
        value.get("schema") == COMPONENT_SCHEMA
        and value.get("decision") == "pass"
        and value.get("subject_digest") == subject_digest
        and value.get("authority") == COMPONENT_AUTHORITIES[name]
        and value.get("evidence_digest") == _sha256(evidence)
        and value.get("evidence_digest") != "absent"
    )


def validate(
    subject_digest: str, production_policy: Any = None, product_root: Path | None = None,
    expected_provider: str = "",
) -> dict[str, Any]:
    path = Path(os.environ.get("HARNESS_STRICT_EVIDENCE", ""))
    try:
        receipt = json.loads(path.read_text(encoding="utf-8")) if str(path) != "." else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        receipt = {}
    if not isinstance(receipt, dict):
        receipt = {}
    base_valid = receipt.get("subject_digest") == subject_digest and all(
        _component_valid(
            receipt.get(name), name=name, subject_digest=subject_digest,
            product_root=product_root,
        )
        for name in COMPONENT_AUTHORITIES
    )
    if production_policy is not None and not isinstance(production_policy, dict):
        return {"decision": "block", "reason": "STRICT_PRODUCTION_POLICY_INVALID"}
    provider_mode = str((production_policy or {}).get("provider_mode") or "").strip()
    if provider_mode not in {"", "synthetic_allowed", "real_required"}:
        return {"decision": "block", "reason": "STRICT_PRODUCTION_POLICY_INVALID"}
    if base_valid and provider_mode == "real_required":
        expected_provider = expected_provider.strip().lower()
        if not expected_provider:
            return {"decision": "block", "reason": "STRICT_EXPECTED_PROVIDER_REQUIRED"}
        provider_preflight = receipt.get("provider_preflight") or {}
        preflight_valid = (
            isinstance(provider_preflight, dict)
            and validate_provider_preflight(
                provider_preflight, subject_digest, expected_provider,
            )["decision"] == "pass"
        )
        if not preflight_valid:
            return {"decision": "block", "reason": "STRICT_PROVIDER_PREFLIGHT_REQUIRED"}
        provider_acceptance = receipt.get("provider_acceptance") or {}
        attempt_receipt = (
            provider_acceptance.get("attempt_receipt")
            if isinstance(provider_acceptance, dict) else None
        )
        evidence_ref = (
            str(provider_acceptance.get("evidence_ref") or "")
            if isinstance(provider_acceptance, dict) else ""
        )
        evidence_path = None
        if product_root is not None and evidence_ref and not Path(evidence_ref).is_absolute():
            try:
                root = product_root.resolve()
                candidate = (root / evidence_ref).resolve()
                candidate.relative_to(root)
                evidence_path = candidate
            except (OSError, ValueError):
                evidence_path = None
        provider_valid = (
            isinstance(provider_acceptance, dict)
            and provider_acceptance.get("decision") == "pass"
            and provider_acceptance.get("provider_mode") == "real"
            and provider_acceptance.get("synthetic_only") is False
            and provider_acceptance.get("provider") == expected_provider
            and bool(evidence_ref)
            and evidence_path is not None
            and isinstance(attempt_receipt, dict)
            and validate_provider_attempt(
                attempt_receipt, provider_preflight, subject_digest,
                expected_provider, evidence_path,
            )["decision"] == "pass"
            and evidence_ref == attempt_receipt.get("evidence_ref")
            and provider_acceptance.get("preflight_receipt_digest")
            == provider_preflight.get("receipt_digest")
            and provider_acceptance.get("attempt_receipt_digest")
            == attempt_receipt.get("receipt_digest")
            and provider_acceptance.get("contract_digest")
            == provider_preflight.get("contract_digest")
            == attempt_receipt.get("contract_digest")
            and provider_acceptance.get("adapter_digest")
            == provider_preflight.get("adapter_digest")
            == attempt_receipt.get("adapter_digest")
            and provider_acceptance.get("canonical_argv_digest")
            == provider_preflight.get("canonical_argv_digest")
            == attempt_receipt.get("canonical_argv_digest")
            and provider_acceptance.get("evidence_digest")
            == attempt_receipt.get("evidence_digest")
            and provider_acceptance.get("attempt_verifier_digest")
            == attempt_receipt.get("attempt_verifier_digest")
        )
        if not provider_valid:
            return {"decision": "block", "reason": "STRICT_REAL_PROVIDER_EVIDENCE_REQUIRED"}
        try:
            harness = Path(__file__).resolve().parents[2]
            sandbox_trust = trust_from_installation(
                harness, "network-sandbox", "harness-network-sandbox",
            )
            provider_trust = trust_from_installation(
                harness, "provider-response", "harness-provider-response",
            )
        except ValueError:
            return {"decision": "block", "reason": "STRICT_PROVIDER_AUTHORITY_REQUIRED"}
        authority_status = validate_provider_authorities(
            receipt.get("provider_authorities"),
            preflight=provider_preflight,
            attempt=attempt_receipt,
            sandbox_trust=sandbox_trust,
            provider_trust=provider_trust,
        )
        if authority_status["decision"] != "pass":
            return {
                "decision": "block",
                "reason": "STRICT_PROVIDER_AUTHORITY_REQUIRED",
                "authority_reason": authority_status["reason"],
            }
    return {
        "decision": "pass" if base_valid else "block",
        "reason": "STRICT_EVIDENCE_OK" if base_valid else "STRICT_EVIDENCE_REQUIRED",
    }
