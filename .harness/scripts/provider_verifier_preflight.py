#!/usr/bin/env python3
"""Offline, parameter-free Provider call-contract verifier."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from harness_output import dump_json
from harness_runtime import canonical_digest, now


CONTRACT_SCHEMA = "harness-provider-call-contract-v1"
TRACE_SCHEMA = "harness-provider-offline-trace-v1"
RECEIPT_SCHEMA = "harness-provider-preflight-receipt-v1"
CALL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
RECEIPT_FIELDS = {
    "schema", "decision", "reason", "subject_digest", "offline", "network_calls",
    "contract", "trace", "contract_digest", "trace_digest", "verifier_digest",
    "observed_call_counts", "judge_usage", "completed_at", "receipt_digest",
}


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("PROVIDER_PREFLIGHT_INPUT_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError("PROVIDER_PREFLIGHT_INPUT_INVALID")
    return value


def _names(value: Any, reason: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and CALL_NAME.fullmatch(item) for item in value
    ):
        raise ValueError(reason)
    return value


def verify(contract: dict[str, Any], trace: dict[str, Any], expected_subject: str) -> dict[str, Any]:
    if contract.get("schema") != CONTRACT_SCHEMA:
        return {"decision": "block", "reason": "PROVIDER_CONTRACT_SCHEMA_INVALID"}
    if trace.get("schema") != TRACE_SCHEMA or trace.get("offline") is not True:
        return {"decision": "block", "reason": "PROVIDER_TRACE_NOT_OFFLINE"}
    if (not expected_subject or contract.get("subject_digest") != expected_subject
            or trace.get("subject_digest") != expected_subject):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_SUBJECT_MISMATCH"}
    try:
        required = _names(contract.get("required_calls"), "PROVIDER_REQUIRED_CALLS_INVALID")
        allowed = _names(contract.get("allowed_calls"), "PROVIDER_ALLOWED_CALLS_INVALID")
        observed = _names(trace.get("calls"), "PROVIDER_TRACE_CALLS_INVALID")
    except ValueError as exc:
        return {"decision": "block", "reason": str(exc)}
    if len(required) != len(set(required)) or len(allowed) != len(set(allowed)):
        return {"decision": "block", "reason": "PROVIDER_CALL_CONTRACT_DUPLICATE"}
    if not set(required).issubset(allowed):
        return {"decision": "block", "reason": "PROVIDER_REQUIRED_NOT_ALLOWED"}
    unexpected = sorted(set(observed) - set(allowed))
    missing = sorted(set(required) - set(observed))
    repeated = sorted(name for name in set(observed) if observed.count(name) > 1)
    if unexpected:
        return {"decision": "block", "reason": "PROVIDER_UNEXPECTED_CALL", "calls": unexpected}
    if missing:
        return {"decision": "block", "reason": "PROVIDER_REQUIRED_CALL_MISSING", "calls": missing}
    if repeated:
        return {"decision": "block", "reason": "PROVIDER_CALL_REPEATED", "calls": repeated}
    contract_snapshot = {
        "schema": CONTRACT_SCHEMA, "subject_digest": expected_subject,
        "required_calls": required, "allowed_calls": allowed,
    }
    trace_snapshot = {
        "schema": TRACE_SCHEMA, "subject_digest": expected_subject,
        "offline": True, "calls": observed,
    }
    contract_digest = canonical_digest(contract_snapshot)
    trace_digest = canonical_digest(trace_snapshot)
    tool_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "decision": "pass",
        "reason": "PROVIDER_OFFLINE_PREFLIGHT_OK",
        "subject_digest": expected_subject,
        "offline": True,
        "network_calls": 0,
        "contract": contract_snapshot,
        "trace": trace_snapshot,
        "contract_digest": contract_digest,
        "trace_digest": trace_digest,
        "verifier_digest": tool_digest,
        "observed_call_counts": {name: observed.count(name) for name in sorted(set(observed))},
        "judge_usage": "unknown",
        "completed_at": now(),
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def validate_receipt(receipt: dict[str, Any], expected_subject: str) -> dict[str, str]:
    if set(receipt) != RECEIPT_FIELDS:
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_RECEIPT_FIELDS_INVALID"}
    claimed_digest = str(receipt.get("receipt_digest") or "")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if claimed_digest != canonical_digest(unsigned):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_RECEIPT_DIGEST_INVALID"}
    contract, trace = receipt.get("contract"), receipt.get("trace")
    if not isinstance(contract, dict) or not isinstance(trace, dict):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_SNAPSHOT_MISSING"}
    recomputed = verify(contract, trace, expected_subject)
    compared = (
        "schema", "decision", "reason", "subject_digest", "offline", "network_calls",
        "contract", "trace", "contract_digest", "trace_digest", "verifier_digest",
        "observed_call_counts", "judge_usage",
    )
    if any(receipt.get(field) != recomputed.get(field) for field in compared):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_RECEIPT_BINDING_INVALID"}
    return {"decision": "pass", "reason": "PROVIDER_PREFLIGHT_RECEIPT_OK"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--expected-subject", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    try:
        result = verify(_load(Path(args.contract)), _load(Path(args.trace)), args.expected_subject)
    except ValueError as exc:
        result = {"decision": "block", "reason": str(exc)}
    if args.output and result.get("decision") == "pass":
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dump_json(result)
    return 0 if result.get("decision") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
