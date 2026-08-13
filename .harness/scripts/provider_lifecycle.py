#!/usr/bin/env python3
"""Git-native acceptance receipts consumed by Work Item terminal transitions."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_attestation import verify_attestation
from harness_output import dump_json

TERMINAL_STATUSES = {"done", "closed", "complete", "completed", "已完成", "完成"}
AUTHORITIES = {"git-receive", "release-gate"}


def build_receipt(repo: Path, *, commit: str, work_item_id: str, provider: str,
                  authority: str, accepted_ref: str) -> dict[str, Any]:
    if authority not in AUTHORITIES:
        raise ValueError("ACCEPTANCE_AUTHORITY_INVALID")
    if not work_item_id or not provider or not accepted_ref.startswith("refs/"):
        raise ValueError("ACCEPTANCE_BINDING_MISSING")
    verified = verify_attestation(repo, commit=commit)
    if verified["decision"] != "pass":
        raise ValueError(verified["reason"])
    attestation = verified["attestation"]
    return {
        "schema": "harness-acceptance-receipt-v1",
        "authority": authority,
        "accepted_ref": accepted_ref,
        "commit_sha": attestation["commit_sha"],
        "attestation_object": verified["object"],
        "task_id": attestation["task_id"],
        "policy_digest": attestation["policy_digest"],
        "result_digest": attestation["result_digest"],
        "work_item_id": work_item_id,
        "provider": provider,
        "accepted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def validate_receipt(receipt: Any, *, work_item_id: str) -> tuple[bool, str]:
    if not isinstance(receipt, dict) or receipt.get("schema") != "harness-acceptance-receipt-v1":
        return False, "ACCEPTANCE_RECEIPT_REQUIRED"
    if receipt.get("authority") not in AUTHORITIES:
        return False, "ACCEPTANCE_AUTHORITY_INVALID"
    required = ("accepted_ref", "commit_sha", "attestation_object", "task_id",
                "policy_digest", "result_digest", "provider", "accepted_at")
    if any(not isinstance(receipt.get(field), str) or not receipt[field] for field in required):
        return False, "ACCEPTANCE_RECEIPT_INVALID"
    if receipt.get("work_item_id") != work_item_id:
        return False, "ACCEPTANCE_WORK_ITEM_MISMATCH"
    return True, "ACCEPTANCE_RECEIPT_VALID"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("receipt",))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--work-item-id", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--authority", choices=tuple(sorted(AUTHORITIES)), required=True)
    parser.add_argument("--accepted-ref", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        receipt = build_receipt(
            Path(args.repo).resolve(), commit=args.commit, work_item_id=args.work_item_id,
            provider=args.provider, authority=args.authority, accepted_ref=args.accepted_ref,
        )
    except ValueError as exc:
        dump_json({"decision": "block", "reason": str(exc)})
        return 1
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    dump_json({"decision": "pass", "reason": "ACCEPTANCE_RECEIPT_READY", "receipt": receipt})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
