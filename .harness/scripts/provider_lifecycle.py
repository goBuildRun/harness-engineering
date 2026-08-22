#!/usr/bin/env python3
"""Git-native acceptance receipts consumed by Work Item terminal transitions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
ACCEPTANCE_POLICY_SCHEMA = "harness-acceptance-policy-v1"
ACCEPTANCE_POLICY_NAMESPACE = "harness-acceptance-policy"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


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


def sign_policy(policy: dict[str, Any], signing_key: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        message = Path(tmp) / "policy.json"
        message.write_text(canonical_policy(policy), encoding="utf-8")
        try:
            subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", str(signing_key),
                 "-n", ACCEPTANCE_POLICY_NAMESPACE, str(message)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise ValueError("ACCEPTANCE_POLICY_SIGNING_FAILED") from exc
        policy["signature"] = message.with_suffix(".json.sig").read_text(encoding="utf-8")
    return policy


def canonical_policy(policy: dict[str, Any]) -> str:
    payload = {key: value for key, value in policy.items() if key != "signature"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def load_acceptance_policy(policy_path: Path, *, allowed_signers: Path,
                           repo_id: str) -> tuple[dict[str, Any], str]:
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("ACCEPTANCE_POLICY_INVALID") from exc
    if not isinstance(policy, dict) or policy.get("schema") != ACCEPTANCE_POLICY_SCHEMA:
        raise ValueError("ACCEPTANCE_POLICY_INVALID")
    if policy.get("repo_id") != repo_id or not repo_id:
        raise ValueError("ACCEPTANCE_POLICY_REPO_MISMATCH")
    task_id = policy.get("attested_task_id")
    accepted_ref = policy.get("accepted_ref")
    authorities = policy.get("allowed_authorities")
    work_items = policy.get("terminal_work_items")
    closure_order = policy.get("closure_order")
    signature_text = policy.get("signature")
    if (not isinstance(policy.get("policy_id"), str) or not policy["policy_id"]
            or not isinstance(task_id, str) or not task_id
            or not isinstance(accepted_ref, str) or not accepted_ref.startswith("refs/")
            or policy.get("accepted_commit_mode") != "exact-ref-tip"
            or not isinstance(authorities, list) or not authorities
            or any(authority not in AUTHORITIES for authority in authorities)
            or len(authorities) != len(set(authorities))
            or not isinstance(work_items, list) or not work_items
            or not isinstance(closure_order, list)
            or not isinstance(signature_text, str) or not signature_text):
        raise ValueError("ACCEPTANCE_POLICY_INVALID")
    item_ids: list[str] = []
    for item in work_items:
        if (not isinstance(item, dict)
                or set(item) != {"id", "provider", "attested_work_item_id"}
                or not isinstance(item.get("id"), str) or not item["id"]
                or not isinstance(item.get("provider"), str) or not item["provider"]
                or not isinstance(item.get("attested_work_item_id"), str)
                or not item["attested_work_item_id"]):
            raise ValueError("ACCEPTANCE_POLICY_INVALID")
        item_ids.append(item["id"])
    if len(item_ids) != len(set(item_ids)) or closure_order != item_ids:
        raise ValueError("ACCEPTANCE_POLICY_INVALID")
    try:
        expires_at = datetime.strptime(str(policy["expires_at"]), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (KeyError, ValueError):
        raise ValueError("ACCEPTANCE_POLICY_INVALID") from None
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("ACCEPTANCE_POLICY_EXPIRED")
    if not allowed_signers.is_file():
        raise ValueError("ACCEPTANCE_POLICY_TRUST_ROOT_MISSING")
    with tempfile.TemporaryDirectory() as tmp:
        signature = Path(tmp) / "policy.sig"
        signature.write_text(signature_text, encoding="utf-8")
        try:
            completed = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers),
                 "-I", "harness-policy", "-n", ACCEPTANCE_POLICY_NAMESPACE,
                 "-s", str(signature)], input=canonical_policy(policy), text=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ValueError("ACCEPTANCE_POLICY_SIGNATURE_TOOL_MISSING") from exc
    if completed.returncode != 0:
        raise ValueError("ACCEPTANCE_POLICY_SIGNATURE_INVALID")
    canonical = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    return policy, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_policy_binding(policy: dict[str, Any], *, task_id: str,
                             attested_work_item_id: str, authority: str,
                             accepted_ref: str, work_item_id: str, provider: str) -> None:
    if policy["attested_task_id"] != task_id:
        raise ValueError("ACCEPTANCE_POLICY_TASK_MISMATCH")
    if authority not in policy["allowed_authorities"]:
        raise ValueError("ACCEPTANCE_AUTHORITY_INVALID")
    if policy["accepted_ref"] != accepted_ref:
        raise ValueError("ACCEPTANCE_REF_MISMATCH")
    allowed = {
        (item["id"], item["provider"], item["attested_work_item_id"])
        for item in policy["terminal_work_items"]
    }
    if (work_item_id, provider, attested_work_item_id) not in allowed:
        raise ValueError("ACCEPTANCE_POLICY_WORK_ITEM_MISMATCH")


def build_receipt(repo: Path, *, commit: str, work_item_id: str, provider: str,
                  authority: str, accepted_ref: str, signing_key: Path | None = None,
                  acceptance_policy: Path, policy_allowed_signers: Path, repo_id: str,
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
    result_work_item = verified.get("result", {}).get("work_item")
    if (not isinstance(result_work_item, dict)
            or not isinstance(result_work_item.get("id"), str) or not result_work_item["id"]
            or not isinstance(result_work_item.get("provider"), str)
            or not result_work_item["provider"]):
        raise ValueError("ACCEPTANCE_ATTESTED_WORK_ITEM_MISSING")
    if result_work_item["provider"] != provider:
        raise ValueError("ACCEPTANCE_PROVIDER_MISMATCH")
    policy, policy_digest = load_acceptance_policy(
        acceptance_policy, allowed_signers=policy_allowed_signers, repo_id=repo_id,
    )
    _validate_policy_binding(
        policy, task_id=attestation["task_id"],
        attested_work_item_id=result_work_item["id"], authority=authority,
        accepted_ref=accepted_ref, work_item_id=work_item_id, provider=provider,
    )
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
        "attested_work_item_id": result_work_item["id"],
        "attested_work_item_provider": result_work_item["provider"],
        "acceptance_policy_id": policy["policy_id"],
        "acceptance_policy_digest": policy_digest,
        "accepted_at": accepted_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (accepted_at + timedelta(seconds=validity_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if signing_key is None or not signing_key.is_file():
        raise ValueError("ACCEPTANCE_SIGNING_KEY_REQUIRED")
    return sign_receipt(receipt, signing_key)


def validate_receipt(receipt: Any, *, work_item_id: str, expected_ref: str,
                     expected_commit: str, configured_provider: str, repo: Path,
                     allowed_signers: Path, acceptance_policy: Path,
                     policy_allowed_signers: Path, repo_id: str) -> tuple[bool, str]:
    if not isinstance(receipt, dict) or receipt.get("schema") != "harness-acceptance-receipt-v1":
        return False, "ACCEPTANCE_RECEIPT_REQUIRED"
    if receipt.get("authority") not in AUTHORITIES:
        return False, "ACCEPTANCE_AUTHORITY_INVALID"
    if receipt.get("work_item_id") != work_item_id:
        return False, "ACCEPTANCE_WORK_ITEM_MISMATCH"
    if receipt.get("provider") != configured_provider:
        return False, "ACCEPTANCE_PROVIDER_MISMATCH"
    required = ("accepted_ref", "commit_sha", "attestation_object", "task_id",
                "policy_digest", "result_digest", "provider", "attested_work_item_id",
                "attested_work_item_provider", "acceptance_policy_id",
                "acceptance_policy_digest", "accepted_at", "expires_at", "signature")
    if any(not isinstance(receipt.get(field), str) or not receipt[field] for field in required):
        return False, "ACCEPTANCE_RECEIPT_INVALID"
    if not expected_ref.startswith("refs/"):
        return False, "ACCEPTANCE_EXPECTED_REF_REQUIRED"
    if not FULL_SHA.fullmatch(expected_commit):
        return False, "ACCEPTANCE_EXPECTED_COMMIT_REQUIRED"
    if receipt.get("accepted_ref") != expected_ref:
        return False, "ACCEPTANCE_REF_MISMATCH"
    if receipt.get("commit_sha") != expected_commit:
        return False, "ACCEPTANCE_COMMIT_MISMATCH"
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
    result_work_item = verified.get("result", {}).get("work_item")
    if (not isinstance(result_work_item, dict)
            or receipt.get("attested_work_item_id") != result_work_item.get("id")
            or receipt.get("attested_work_item_provider") != result_work_item.get("provider")):
        return False, "ACCEPTANCE_ATTESTED_WORK_ITEM_MISMATCH"
    if result_work_item.get("provider") != configured_provider:
        return False, "ACCEPTANCE_PROVIDER_MISMATCH"
    try:
        acceptance_policy, acceptance_policy_digest = load_acceptance_policy(
            acceptance_policy, allowed_signers=policy_allowed_signers, repo_id=repo_id,
        )
        _validate_policy_binding(
            acceptance_policy, task_id=str(receipt["task_id"]),
            attested_work_item_id=str(receipt["attested_work_item_id"]),
            authority=str(receipt["authority"]), accepted_ref=str(receipt["accepted_ref"]),
            work_item_id=work_item_id, provider=str(receipt["provider"]),
        )
    except ValueError as exc:
        return False, str(exc)
    if (receipt.get("acceptance_policy_id") != acceptance_policy["policy_id"]
            or receipt.get("acceptance_policy_digest") != acceptance_policy_digest):
        return False, "ACCEPTANCE_POLICY_MISMATCH"
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
        ref_tip = subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{receipt['accepted_ref']}^{{commit}}"],
            cwd=repo, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False, "ACCEPTANCE_REF_MISMATCH"
    if ref_tip != receipt["commit_sha"]:
        return False, "ACCEPTANCE_COMMIT_NOT_REF_TIP"
    return True, "ACCEPTANCE_RECEIPT_VALID"


def validate_terminal_receipt(receipt: Any, *, work_item_id: str, expected_ref: str,
                              expected_commit: str, configured_provider: str,
                              repo: Path) -> tuple[bool, str]:
    try:
        repo_id = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=repo, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False, "ACCEPTANCE_REPO_ID_MISSING"
    allowed = os.environ.get("HARNESS_ACCEPTANCE_ALLOWED_SIGNERS", "")
    policy_path = os.environ.get("HARNESS_ACCEPTANCE_POLICY", "")
    policy_allowed = os.environ.get("HARNESS_ACCEPTANCE_POLICY_ALLOWED_SIGNERS", "")
    return validate_receipt(
        receipt, work_item_id=work_item_id, expected_ref=expected_ref,
        expected_commit=expected_commit, configured_provider=configured_provider, repo=repo,
        allowed_signers=Path(allowed) if allowed else Path("/nonexistent"),
        acceptance_policy=Path(policy_path) if policy_path else Path("/nonexistent"),
        policy_allowed_signers=Path(policy_allowed) if policy_allowed else Path("/nonexistent"),
        repo_id=repo_id,
    )


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
    parser.add_argument("--acceptance-policy", required=True)
    parser.add_argument("--policy-allowed-signers", required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        receipt = build_receipt(
            Path(args.repo).resolve(), commit=args.commit, work_item_id=args.work_item_id,
            provider=args.provider, authority=args.authority, accepted_ref=args.accepted_ref,
            signing_key=Path(args.signing_key), acceptance_policy=Path(args.acceptance_policy),
            policy_allowed_signers=Path(args.policy_allowed_signers), repo_id=args.repo_id,
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
