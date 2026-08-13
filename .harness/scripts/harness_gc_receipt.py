#!/usr/bin/env python3
"""Signed, commit-bound independent GC review receipts."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_runtime import canonical_digest
from harness_output import dump_json

SCHEMA = "harness-gc-receipt-v1"
NAMESPACE = "harness-gc-review"
REF_PREFIX = "refs/harness/gc"


def receipt_ref(commit: str) -> str:
    return f"{REF_PREFIX}/{commit}"


def canonical_receipt(receipt: dict[str, Any]) -> str:
    return json.dumps(
        {key: value for key, value in receipt.items() if key != "signature"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n"


def _valid_count(value: Any, *, maximum: int | None = None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool) and value >= 0
            and (maximum is None or value <= maximum))


def build_receipt(*, commit: str, task_id: str, policy_digest: str,
                  context: dict[str, Any], triggers: list[str], gc_result: dict[str, Any],
                  signing_key: Path) -> dict[str, Any]:
    telemetry = gc_result.get("telemetry") or {}
    if (gc_result.get("decision") != "pass" or gc_result.get("role") != "gc-sweeper"
            or gc_result.get("independent") is not True
            or gc_result.get("task_id") != task_id
            or gc_result.get("subject_digest") != commit
            or gc_result.get("policy_digest") != policy_digest
            or not _valid_count(telemetry.get("agent_calls"), maximum=1)
            or telemetry.get("agent_calls") != 1
            or not _valid_count(telemetry.get("context_chars"))
            or not _valid_count(telemetry.get("duration_ms"))):
        raise ValueError("GC_RECEIPT_RESULT_INVALID")
    deferred = gc_result.get("deferred_work_items") or []
    if gc_result.get("deferred_findings", 0) and not deferred:
        raise ValueError("GC_RECEIPT_DEFERRED_WORK_ITEM_REQUIRED")
    receipt = {
        "schema": SCHEMA,
        "commit_sha": commit,
        "task_id": task_id,
        "policy_digest": policy_digest,
        "context_digest": canonical_digest(context),
        "triggers": sorted(set(triggers)),
        "findings": int(gc_result.get("findings") or 0),
        "remediated": int(gc_result.get("remediated") or 0),
        "deferred_work_items": deferred,
        "telemetry": telemetry,
        "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if not signing_key.is_file():
        raise ValueError("GC_RECEIPT_SIGNING_KEY_REQUIRED")
    with tempfile.TemporaryDirectory() as tmp:
        message = Path(tmp) / "gc-receipt.json"
        message.write_text(canonical_receipt(receipt), encoding="utf-8")
        try:
            subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", str(signing_key), "-n", NAMESPACE,
                 str(message)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise ValueError("GC_RECEIPT_SIGNING_FAILED") from exc
        receipt["signature"] = message.with_suffix(".json.sig").read_text(encoding="utf-8")
    return receipt


def store_receipt(repo: Path, receipt: dict[str, Any]) -> str:
    raw = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        obj = subprocess.check_output(
            ["git", "hash-object", "-w", "--stdin"], cwd=repo, input=raw, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        subprocess.run(
            ["git", "update-ref", receipt_ref(str(receipt["commit_sha"])), obj], cwd=repo,
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ValueError("GC_RECEIPT_STORE_FAILED") from exc
    return obj


def verify_receipt(repo: Path, *, commit: str, task_id: str, policy_digest: str,
                   context: dict[str, Any], triggers: list[str], allowed_signers: Path
                   ) -> dict[str, Any]:
    try:
        obj = subprocess.check_output(
            ["git", "rev-parse", "--verify", receipt_ref(commit)], cwd=repo, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        raw = subprocess.check_output(
            ["git", "cat-file", "blob", obj], cwd=repo, text=True, stderr=subprocess.DEVNULL,
        )
        receipt = json.loads(raw)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {"decision": "block", "reason": "GC_RECEIPT_MISSING"}
    expected = {
        "schema": SCHEMA, "commit_sha": commit, "task_id": task_id,
        "policy_digest": policy_digest, "context_digest": canonical_digest(context),
        "triggers": sorted(set(triggers)),
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        return {"decision": "block", "reason": "GC_RECEIPT_BINDING_MISMATCH"}
    telemetry = receipt.get("telemetry") or {}
    if (telemetry.get("agent_calls") != 1 or not _valid_count(telemetry.get("context_chars"))
            or not _valid_count(telemetry.get("duration_ms"))
            or not allowed_signers.is_file()):
        return {"decision": "block", "reason": "GC_RECEIPT_INVALID"}
    with tempfile.TemporaryDirectory() as tmp:
        signature = Path(tmp) / "gc.sig"
        signature.write_text(str(receipt.get("signature") or ""), encoding="utf-8")
        try:
            completed = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers), "-I", "harness",
                 "-n", NAMESPACE, "-s", str(signature)], input=canonical_receipt(receipt),
                text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return {"decision": "block", "reason": "GC_RECEIPT_SIGNATURE_TOOL_MISSING"}
    if completed.returncode != 0:
        return {"decision": "block", "reason": "GC_RECEIPT_SIGNATURE_INVALID"}
    return {"decision": "pass", "reason": "GC_RECEIPT_VALID", "object": obj,
            "receipt": receipt}


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal independent GC receipt signer")
    parser.add_argument("sign", choices=("sign",))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--policy-digest", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--gc-result", required=True)
    parser.add_argument("--signing-key", required=True)
    args = parser.parse_args()
    try:
        context = json.loads(Path(args.context).read_text(encoding="utf-8"))
        result = json.loads(Path(args.gc_result).read_text(encoding="utf-8"))
        receipt = build_receipt(
            commit=args.commit, task_id=args.task_id, policy_digest=args.policy_digest,
            context=context, triggers=list(context.get("triggers") or []), gc_result=result,
            signing_key=Path(args.signing_key),
        )
        obj = store_receipt(Path(args.repo).resolve(), receipt)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        dump_json({"decision": "block", "reason": str(exc)})
        return 1
    dump_json({"decision": "pass", "reason": "GC_RECEIPT_STORED", "object": obj,
               "ref": receipt_ref(args.commit)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
