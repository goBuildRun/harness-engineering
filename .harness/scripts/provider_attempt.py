#!/usr/bin/env python3
"""One-shot, budget-bound Provider execution after offline preflight."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from harness_output import dump_json
from harness_runtime import canonical_digest, load_result, now, result_path, subject_for
from harness_task_resolution import valid_task_id
from harness_timing import budget_status
from process_control import run_process_group
from provider_verifier_preflight import validate_receipt as validate_preflight
from worktree_baseline import changed_since_baseline


SCHEMA = "harness-provider-attempt-v2"
FIELDS = {
    "schema", "decision", "reason", "subject_digest", "provider", "mode",
    "provider_process_attempts", "preflight_receipt_digest", "command_digest",
    "evidence_ref", "evidence_digest", "duration_ms", "completed_at", "receipt_digest",
}


def _evidence_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


def _write_claim(path: Path, payload: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return True


def run_once(
    state_path: Path, preflight: dict[str, Any], expected_subject: str, provider: str,
    evidence_path: Path, evidence_ref: str, command: list[str], timeout_seconds: float,
    runner: Callable[..., Any] = run_process_group,
) -> dict[str, Any]:
    verified = validate_preflight(preflight, expected_subject)
    if verified["decision"] != "pass":
        return {"decision": "block", "reason": verified["reason"]}
    if not provider.strip() or not command or timeout_seconds <= 0:
        return {"decision": "block", "reason": "PROVIDER_ATTEMPT_INPUT_INVALID"}
    evidence_before = _evidence_digest(evidence_path)
    claim = {
        "schema": SCHEMA, "state": "claimed", "subject_digest": expected_subject,
        "preflight_receipt_digest": preflight["receipt_digest"], "claimed_at": now(),
    }
    if not _write_claim(state_path, claim):
        return {"decision": "block", "reason": "PROVIDER_ATTEMPT_LIMIT_EXCEEDED"}
    started = time.monotonic()
    try:
        completed = runner(command, cwd=None, env=os.environ.copy(), timeout=timeout_seconds)
        returncode = int(completed.returncode)
    except Exception:
        returncode = 125
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    evidence_digest = _evidence_digest(evidence_path)
    evidence_ok = evidence_digest != "absent" and evidence_digest != evidence_before
    decision = "pass" if returncode == 0 and evidence_ok else "block"
    reason = (
        "PROVIDER_ATTEMPT_OK" if decision == "pass"
        else "PROVIDER_EVIDENCE_MISSING" if returncode == 0 and evidence_digest == "absent"
        else "PROVIDER_EVIDENCE_STALE" if returncode == 0 else "PROVIDER_PROCESS_FAILED"
    )
    receipt = {
        "schema": SCHEMA, "decision": decision, "reason": reason,
        "subject_digest": expected_subject, "provider": provider.strip(), "mode": "real",
        "provider_process_attempts": 1,
        "preflight_receipt_digest": preflight["receipt_digest"],
        "command_digest": canonical_digest(command), "evidence_ref": evidence_ref,
        "evidence_digest": evidence_digest,
        "duration_ms": duration_ms, "completed_at": now(),
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    state_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def validate_receipt(
    receipt: dict[str, Any], preflight: dict[str, Any], expected_subject: str,
    evidence_path: Path | None = None,
) -> dict[str, str]:
    if set(receipt) != FIELDS:
        return {"decision": "block", "reason": "PROVIDER_ATTEMPT_RECEIPT_FIELDS_INVALID"}
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    valid = (
        receipt.get("receipt_digest") == canonical_digest(unsigned)
        and receipt.get("decision") == "pass"
        and receipt.get("subject_digest") == expected_subject
        and receipt.get("mode") == "real"
        and receipt.get("provider_process_attempts") == 1
        and receipt.get("preflight_receipt_digest") == preflight.get("receipt_digest")
        and isinstance(receipt.get("evidence_ref"), str)
        and bool(receipt["evidence_ref"].strip())
        and isinstance(receipt.get("evidence_digest"), str)
        and len(receipt["evidence_digest"]) == 64
        and all(character in "0123456789abcdef" for character in receipt["evidence_digest"])
        and (
            evidence_path is None
            or _evidence_digest(evidence_path) == receipt["evidence_digest"]
        )
    )
    return {
        "decision": "pass" if valid else "block",
        "reason": "PROVIDER_ATTEMPT_RECEIPT_OK" if valid else "PROVIDER_ATTEMPT_RECEIPT_INVALID",
    }


def _remaining_provider_seconds(result: dict[str, Any]) -> float:
    cycle = result.get("cycle") or {}
    if budget_status(cycle)["decision"] != "pass" or cycle.get("current_stage") != "deploy_provider":
        return 0.0
    state = (cycle.get("stages") or {}).get("deploy_provider") or {}
    attempts = state.get("attempts") or []
    if not attempts:
        return 0.0
    current_ms = max(0, int(time.time() * 1000) - int(attempts[-1]["started_epoch_ms"]))
    remaining_ms = int(cycle["stage_budgets_ms"]["deploy_provider"]) - int(state.get("wall_ms") or 0) - current_ms
    return max(0.0, remaining_ms / 1000)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--expected-subject", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--evidence-ref", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    product = Path(args.product_root).resolve()
    task_path = result_path(product, args.task_id) if valid_task_id(args.task_id) else Path()
    try:
        result = load_result(task_path)
        baseline = task_path.parent / "worktree_baseline.json"
        subject = subject_for(product, changed_since_baseline(product, baseline))
        preflight = json.loads(Path(args.preflight).read_text(encoding="utf-8"))
        evidence = (product / args.evidence_ref).resolve()
        evidence.relative_to(product)
        if subject != args.expected_subject:
            raise ValueError("PROVIDER_ATTEMPT_SUBJECT_MISMATCH")
        outcome = run_once(
            task_path.parent / "provider-attempt.json", preflight, subject, args.provider,
            evidence, args.evidence_ref, command, _remaining_provider_seconds(result),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        outcome = {"decision": "block", "reason": str(exc) or "PROVIDER_ATTEMPT_INVALID"}
    dump_json(outcome)
    return 0 if outcome.get("decision") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
