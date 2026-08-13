#!/usr/bin/env python3
"""Build provider lifecycle receipts from successful commit-bound Harness results."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from harness_output import dump_json
from harness_runtime import canonical_digest
from harness_schema import validate_result


def validated_result_binding(result: dict[str, Any], *, commit: str) -> dict[str, str]:
    issues = validate_result(result)
    subject = result.get("subject") or {}
    if issues:
        raise ValueError("RESULT_SCHEMA_INVALID: " + ",".join(issues))
    if result.get("decision") != "pass" or result.get("state") != "validated":
        raise ValueError("HARNESS_RESULT_NOT_PASS")
    if subject.get("kind") != "commit" or subject.get("digest") != commit:
        raise ValueError("HARNESS_RESULT_SUBJECT_MISMATCH")
    task_id = str(result.get("task_id") or "")
    policy_digest = str(result.get("policy_digest") or "")
    binding_digest = str(result.get("binding_digest") or "")
    if not task_id or not policy_digest or not binding_digest:
        raise ValueError("HARNESS_RESULT_BINDING_MISSING")
    return {"task_id": task_id, "policy_digest": policy_digest,
            "binding_digest": binding_digest, "result_digest": canonical_digest(result)}


def build_receipt(result: dict[str, Any], *, event: str, commit: str,
                  repository: str, run_id: str) -> dict[str, Any]:
    binding = validated_result_binding(result, commit=commit)
    work_item = result.get("work_item") or {}
    if not isinstance(work_item, dict) or not work_item.get("id") or not work_item.get("provider"):
        raise ValueError("WORK_ITEM_BINDING_MISSING")
    if event not in {"merge", "release"}:
        raise ValueError("LIFECYCLE_EVENT_INVALID")
    now = datetime.now(timezone.utc)
    return {
        "schema_version": 1, "source": "live", "event": event,
        "work_item_id": str(work_item["id"]), "provider": str(work_item.get("provider") or ""),
        **binding,
        "commit_sha": commit, "repository": repository, "run_id": str(run_id),
        "required_check": "harness-commit-acceptance", "check_status": "success",
        "probed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("receipt",))
    parser.add_argument("--result", required=True)
    parser.add_argument("--event", choices=("merge", "release"), required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = json.loads(Path(args.result).read_text(encoding="utf-8"))
        receipt = build_receipt(
            result, event=args.event, commit=args.commit,
            repository=args.repository, run_id=args.run_id,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        dump_json({"decision": "block", "reason": str(exc)})
        return 1
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    dump_json({"decision": "pass", "reason": "LIFECYCLE_RECEIPT_READY", "receipt": receipt})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
