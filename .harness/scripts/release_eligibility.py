#!/usr/bin/env python3
"""Build a release eligibility receipt from a validated commit result."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness_output import dump_json
from provider_lifecycle import validated_result_binding


def validate_receipt(receipt: dict, *, repository: str, commit: str,
                     required_run_id: str, task_id: str = "",
                     policy_digest: str = "") -> tuple[bool, str]:
    expected = {
        "schema_version": 1, "kind": "release-eligibility", "decision": "pass",
        "repository": repository, "commit_sha": commit,
        "required_run_id": str(required_run_id),
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        return False, "RELEASE_ELIGIBILITY_TARGET_MISMATCH"
    if not str(required_run_id).isdigit() or int(required_run_id) <= 0:
        return False, "RELEASE_ELIGIBILITY_RUN_ID_INVALID"
    if task_id and receipt.get("task_id") != task_id:
        return False, "RELEASE_ELIGIBILITY_TASK_MISMATCH"
    if policy_digest and receipt.get("policy_digest") != policy_digest:
        return False, "RELEASE_ELIGIBILITY_POLICY_MISMATCH"
    if len(commit) != 40 or any(char not in "0123456789abcdefABCDEF" for char in commit):
        return False, "RELEASE_ELIGIBILITY_COMMIT_INVALID"
    for field in ("policy_digest", "binding_digest", "result_digest"):
        value = str(receipt.get(field) or "")
        if len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value):
            return False, f"RELEASE_ELIGIBILITY_{field.upper()}_INVALID"
    if not str(receipt.get("task_id") or ""):
        return False, "RELEASE_ELIGIBILITY_TASK_MISSING"
    return True, "RELEASE_ELIGIBILITY_VALID"


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--result", required=True)
    build.add_argument("--commit", required=True)
    build.add_argument("--repository", required=True)
    build.add_argument("--required-run-id", required=True)
    build.add_argument("--output", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--commit", required=True)
    verify.add_argument("--repository", required=True)
    verify.add_argument("--required-run-id", required=True)
    verify.add_argument("--task-id", default="")
    verify.add_argument("--policy-digest", default="")
    args = parser.parse_args()
    if args.command == "verify":
        try:
            receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            dump_json({"decision": "block", "reason": "RELEASE_ELIGIBILITY_RECEIPT_INVALID"})
            return 1
        valid, reason = validate_receipt(
            receipt, repository=args.repository, commit=args.commit,
            required_run_id=args.required_run_id, task_id=args.task_id,
            policy_digest=args.policy_digest,
        )
        dump_json({"decision": "pass" if valid else "block", "reason": reason})
        return 0 if valid else 1
    try:
        result = json.loads(Path(args.result).read_text(encoding="utf-8"))
        binding = validated_result_binding(result, commit=args.commit)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        dump_json({"decision": "block", "reason": str(exc)})
        return 1
    receipt = {
        "schema_version": 1, "kind": "release-eligibility", "decision": "pass",
        "repository": args.repository, "commit_sha": args.commit,
        "required_run_id": str(args.required_run_id), **binding,
    }
    target = Path(args.output)
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dump_json({"decision": "pass", "reason": "RELEASE_ELIGIBILITY_READY", "receipt": receipt})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
