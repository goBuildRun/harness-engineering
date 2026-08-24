#!/usr/bin/env python3
"""Validate signed acceptance receipts that prove closure predecessors."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acceptance_trust import verify_pinned_signature


SIGNATURE_NAMESPACE = "harness-acceptance"


def canonical_receipt(receipt: dict[str, Any]) -> str:
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def validate_closure_order(
    policy: dict[str, Any], *, work_item_id: str,
    predecessor_receipts: tuple[dict[str, Any], ...],
    target_receipt: dict[str, Any], allowed_signers: Path,
    signer_fingerprint: str,
) -> tuple[int, list[str]]:
    order = policy["closure_order"]
    try:
        index = order.index(work_item_id)
    except ValueError:
        raise ValueError("ACCEPTANCE_POLICY_WORK_ITEM_MISMATCH") from None
    predecessors = order[:index]
    if len(predecessor_receipts) != len(predecessors):
        raise ValueError("ACCEPTANCE_CLOSURE_RECEIPTS_REQUIRED")
    items = {item["id"]: item for item in policy["terminal_work_items"]}
    for position, (expected_id, receipt) in enumerate(
        zip(predecessors, predecessor_receipts, strict=True)
    ):
        expected_item = items[expected_id]
        if (
            receipt.get("schema") != "harness-acceptance-receipt-v1"
            or receipt.get("authority") not in policy["allowed_authorities"]
            or receipt.get("work_item_id") != expected_id
            or receipt.get("provider") != expected_item["provider"]
            or receipt.get("attested_work_item_id")
            != expected_item["attested_work_item_id"]
            or receipt.get("task_id") != policy["attested_task_id"]
            or receipt.get("accepted_ref") != target_receipt.get("accepted_ref")
            or receipt.get("commit_sha") != target_receipt.get("commit_sha")
            or receipt.get("acceptance_policy_id") != policy["policy_id"]
            or receipt.get("acceptance_policy_digest")
            != target_receipt.get("acceptance_policy_digest")
            or receipt.get("closure_index") != position
            or receipt.get("predecessor_work_item_ids") != predecessors[:position]
            or not isinstance(receipt.get("signature"), str)
        ):
            raise ValueError("ACCEPTANCE_PREDECESSOR_RECEIPT_INVALID")
        try:
            expires_at = datetime.strptime(
                str(receipt.get("expires_at") or ""), "%Y-%m-%dT%H:%M:%SZ",
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("ACCEPTANCE_PREDECESSOR_RECEIPT_INVALID") from None
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("ACCEPTANCE_PREDECESSOR_RECEIPT_EXPIRED")
        verify_pinned_signature(
            message=canonical_receipt(receipt),
            signature_text=receipt["signature"],
            allowed_signers=allowed_signers,
            principal="harness",
            namespace=SIGNATURE_NAMESPACE,
            fingerprint=signer_fingerprint,
            missing_reason="ACCEPTANCE_TRUST_ROOT_MISSING",
            mismatch_reason="ACCEPTANCE_SIGNER_MISMATCH",
            invalid_reason="ACCEPTANCE_PREDECESSOR_SIGNATURE_INVALID",
            tool_reason="ACCEPTANCE_SIGNATURE_TOOL_MISSING",
        )
    return index, predecessors
