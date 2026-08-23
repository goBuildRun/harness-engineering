#!/usr/bin/env python3
"""Validate the lifecycle readback that closes the Story root clock."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness_execution_authority import (
    AuthorityTrust,
    trust_from_installation,
    validate_receipt as validate_authority_receipt,
)
from harness_runtime import canonical_digest, now


SCHEMA = "harness-lifecycle-readback-v1"
FIELDS = {
    "schema", "decision", "reason", "task_id", "work_item_id", "provider",
    "status", "readback", "candidate_digest", "commit", "completed_at",
    "receipt_digest",
}


def build_local_receipt(
    *, task_id: str, work_item_id: str, candidate_digest: str, commit: str,
) -> dict[str, Any]:
    receipt = {
        "schema": SCHEMA,
        "decision": "pass",
        "reason": "LOCAL_READY_TO_RELEASE_READBACK",
        "task_id": task_id,
        "work_item_id": work_item_id,
        "provider": "noop",
        "status": "ready_to_release",
        "readback": "pass",
        "candidate_digest": candidate_digest,
        "commit": commit,
        "completed_at": now(),
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def validate(
    receipt: dict[str, Any], *, task_id: str, work_item_id: str, provider: str,
    candidate_digest: str, commit: str, trust: AuthorityTrust | None = None,
) -> dict[str, str]:
    if provider != "noop":
        claims = {
            "task_id": task_id,
            "work_item_id": work_item_id,
            "status": "ready_to_release",
            "readback": "pass",
            "candidate_digest": candidate_digest,
            "commit": commit,
        }
        try:
            trust = trust or trust_from_installation(
                Path(__file__).resolve().parents[2],
                "lifecycle-readback", "harness-lifecycle-readback",
            )
        except ValueError as exc:
            return {
                "decision": "block",
                "reason": "LIFECYCLE_TRUSTED_READBACK_REQUIRED",
                "authority_reason": str(exc),
            }
        authority = validate_authority_receipt(
            receipt,
            trust=trust,
            authority="lifecycle-readback",
            action="ready-to-release-readback",
            subject_digest=candidate_digest,
            provider=provider,
            input_digest=canonical_digest({
                "task_id": task_id,
                "work_item_id": work_item_id,
                "candidate_digest": candidate_digest,
                "commit": commit,
                "provider": provider,
            }),
            output_digest=canonical_digest({
                "status": "ready_to_release", "readback": "pass",
            }),
            claims=claims,
        )
        return {
            "decision": authority["decision"],
            "reason": (
                "LIFECYCLE_READBACK_VALID"
                if authority["decision"] == "pass"
                else "LIFECYCLE_TRUSTED_READBACK_REQUIRED"
            ),
            **(
                {} if authority["decision"] == "pass"
                else {"authority_reason": authority["reason"]}
            ),
        }
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    valid = (
        set(receipt) == FIELDS
        and receipt.get("receipt_digest") == canonical_digest(unsigned)
        and receipt.get("schema") == SCHEMA
        and receipt.get("decision") == "pass"
        and receipt.get("task_id") == task_id
        and receipt.get("work_item_id") == work_item_id
        and receipt.get("provider") == provider
        and receipt.get("status") == "ready_to_release"
        and receipt.get("readback") == "pass"
        and receipt.get("candidate_digest") == candidate_digest
        and receipt.get("commit") == commit
    )
    return {
        "decision": "pass" if valid else "block",
        "reason": "LIFECYCLE_READBACK_VALID" if valid else "LIFECYCLE_READBACK_INVALID",
    }
