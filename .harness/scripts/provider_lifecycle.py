#!/usr/bin/env python3
"""Git-native acceptance receipts consumed by Work Item terminal transitions."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from harness_attestation import verify_attestation
from harness_output import dump_json

TERMINAL_STATUSES = {"done", "closed", "complete", "completed", "已完成", "完成"}
AUTHORITIES = {"git-receive", "release-gate"}
SIGNATURE_NAMESPACE = "harness-acceptance"


def canonical_receipt(receipt: dict[str, Any]) -> str:
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def sign_receipt(receipt: dict[str, Any], signing_key: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        message = Path(tmp) / "receipt.json"
        message.write_text(canonical_receipt(receipt), encoding="utf-8")
        try:
            subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", str(signing_key), "-n", SIGNATURE_NAMESPACE,
                 str(message)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise ValueError("ACCEPTANCE_SIGNING_FAILED") from exc
        receipt["signature"] = message.with_suffix(".json.sig").read_text(encoding="utf-8")
    return receipt


def build_receipt(repo: Path, *, commit: str, work_item_id: str, provider: str,
                  authority: str, accepted_ref: str, signing_key: Path | None = None,
                  validity_seconds: int = 86400) -> dict[str, Any]:
    if authority not in AUTHORITIES:
        raise ValueError("ACCEPTANCE_AUTHORITY_INVALID")
    if not work_item_id or not provider or not accepted_ref.startswith("refs/"):
        raise ValueError("ACCEPTANCE_BINDING_MISSING")
    if validity_seconds <= 0:
        raise ValueError("ACCEPTANCE_VALIDITY_INVALID")
    verified = verify_attestation(repo, commit=commit)
    if verified["decision"] != "pass":
        raise ValueError(verified["reason"])
    attestation = verified["attestation"]
    accepted_at = datetime.now(timezone.utc)
    receipt = {
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
        "accepted_at": accepted_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (accepted_at + timedelta(seconds=validity_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if signing_key is None or not signing_key.is_file():
        raise ValueError("ACCEPTANCE_SIGNING_KEY_REQUIRED")
    return sign_receipt(receipt, signing_key)


def validate_receipt(receipt: Any, *, work_item_id: str, repo: Path,
                     allowed_signers: Path) -> tuple[bool, str]:
    if not isinstance(receipt, dict) or receipt.get("schema") != "harness-acceptance-receipt-v1":
        return False, "ACCEPTANCE_RECEIPT_REQUIRED"
    if receipt.get("authority") not in AUTHORITIES:
        return False, "ACCEPTANCE_AUTHORITY_INVALID"
    required = ("accepted_ref", "commit_sha", "attestation_object", "task_id",
                "policy_digest", "result_digest", "provider", "accepted_at", "expires_at", "signature")
    if any(not isinstance(receipt.get(field), str) or not receipt[field] for field in required):
        return False, "ACCEPTANCE_RECEIPT_INVALID"
    if receipt.get("work_item_id") != work_item_id:
        return False, "ACCEPTANCE_WORK_ITEM_MISMATCH"
    try:
        expires_at = datetime.strptime(str(receipt["expires_at"]), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False, "ACCEPTANCE_RECEIPT_INVALID"
    if expires_at <= datetime.now(timezone.utc):
        return False, "ACCEPTANCE_RECEIPT_EXPIRED"
    if not allowed_signers.is_file():
        return False, "ACCEPTANCE_TRUST_ROOT_MISSING"
    verified = verify_attestation(repo, commit=str(receipt["commit_sha"]))
    if verified.get("decision") != "pass" or verified.get("object") != receipt["attestation_object"]:
        return False, "ACCEPTANCE_ATTESTATION_INVALID"
    attestation = verified["attestation"]
    for field in ("task_id", "policy_digest", "result_digest"):
        if receipt.get(field) != attestation.get(field):
            return False, "ACCEPTANCE_ATTESTATION_MISMATCH"
    with tempfile.TemporaryDirectory() as tmp:
        signature = Path(tmp) / "receipt.sig"
        signature.write_text(str(receipt["signature"]), encoding="utf-8")
        try:
            completed = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers), "-I", "harness",
                 "-n", SIGNATURE_NAMESPACE, "-s", str(signature)],
                input=canonical_receipt(receipt), text=True, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return False, "ACCEPTANCE_SIGNATURE_TOOL_MISSING"
    if completed.returncode != 0:
        return False, "ACCEPTANCE_SIGNATURE_INVALID"
    try:
        contained = subprocess.run(
            ["git", "merge-base", "--is-ancestor", str(receipt["commit_sha"]),
             str(receipt["accepted_ref"])], cwd=repo, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    except FileNotFoundError:
        contained = False
    if not contained:
        return False, "ACCEPTANCE_REF_MISMATCH"
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
    parser.add_argument("--signing-key", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        receipt = build_receipt(
            Path(args.repo).resolve(), commit=args.commit, work_item_id=args.work_item_id,
            provider=args.provider, authority=args.authority, accepted_ref=args.accepted_ref,
            signing_key=Path(args.signing_key),
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
