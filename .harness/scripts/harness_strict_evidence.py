#!/usr/bin/env python3
"""Subject-bound strict deployment and Provider evidence validation."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from provider_verifier_preflight import validate_receipt as validate_provider_preflight
from provider_attempt import validate_receipt as validate_provider_attempt


def validate(
    subject_digest: str, production_policy: Any = None, product_root: Path | None = None,
) -> dict[str, Any]:
    path = Path(os.environ.get("HARNESS_STRICT_EVIDENCE", ""))
    try:
        receipt = json.loads(path.read_text(encoding="utf-8")) if str(path) != "." else {}
    except (OSError, json.JSONDecodeError):
        receipt = {}
    required = ("browser_qa", "deployment", "rollback")
    base_valid = (
        receipt.get("subject_digest") == subject_digest
        and all((receipt.get(name) or {}).get("decision") == "pass" for name in required)
    )
    if production_policy is not None and not isinstance(production_policy, dict):
        return {"decision": "block", "reason": "STRICT_PRODUCTION_POLICY_INVALID"}
    provider_mode = str((production_policy or {}).get("provider_mode") or "").strip()
    if provider_mode not in {"", "synthetic_allowed", "real_required"}:
        return {"decision": "block", "reason": "STRICT_PRODUCTION_POLICY_INVALID"}
    if base_valid and provider_mode == "real_required":
        provider_preflight = receipt.get("provider_preflight") or {}
        preflight_valid = (
            isinstance(provider_preflight, dict)
            and validate_provider_preflight(provider_preflight, subject_digest)["decision"] == "pass"
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
        if product_root is not None and evidence_ref:
            try:
                candidate = (product_root.resolve() / evidence_ref).resolve()
                candidate.relative_to(product_root.resolve())
                evidence_path = candidate
            except ValueError:
                evidence_path = None
        provider_valid = (
            isinstance(provider_acceptance, dict)
            and provider_acceptance.get("decision") == "pass"
            and provider_acceptance.get("provider_mode") == "real"
            and provider_acceptance.get("synthetic_only") is False
            and bool(evidence_ref)
            and evidence_path is not None
            and isinstance(attempt_receipt, dict)
            and validate_provider_attempt(
                attempt_receipt, provider_preflight, subject_digest, evidence_path,
            )["decision"] == "pass"
            and evidence_ref == attempt_receipt.get("evidence_ref")
        )
        if not provider_valid:
            return {"decision": "block", "reason": "STRICT_REAL_PROVIDER_EVIDENCE_REQUIRED"}
    return {
        "decision": "pass" if base_valid else "block",
        "reason": "STRICT_EVIDENCE_OK" if base_valid else "STRICT_EVIDENCE_REQUIRED",
    }
