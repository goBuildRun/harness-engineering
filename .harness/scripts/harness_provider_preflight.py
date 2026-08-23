#!/usr/bin/env python3
"""Execute and bind an offline Provider adapter call-contract trace."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from harness_output import dump_json
from harness_gate_inputs import execution_dependency_digest
from harness_gate_argv import command_executes_path
from harness_gate_manifest import GateInputError
from harness_runtime import canonical_digest, now
from process_control import run_process_group


CONTRACT_SCHEMA = "harness-provider-call-contract-v2"
TRACE_SCHEMA = "harness-provider-offline-trace-v2"
RECEIPT_SCHEMA = "harness-provider-preflight-receipt-v3"
CALL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
RECEIPT_FIELDS = {
    "schema", "decision", "reason", "subject_digest", "provider", "offline",
    "network_calls", "contract", "trace", "contract_digest", "trace_digest",
    "adapter_digest", "canonical_argv_digest", "verifier_digest",
    "execution_dependency_digest", "execution_environment_digest",
    "observed_call_counts", "judge_usage", "completed_at", "receipt_digest",
}
ENVIRONMENT_EXCLUDES = frozenset({
    "HARNESS_PROVIDER_OFFLINE_TRACE", "HARNESS_PROVIDER_EXECUTION_MODE",
    "HARNESS_PROVIDER_EXPECTED_SUBJECT", "HARNESS_PROVIDER_NAME",
    "OLDPWD", "PWD", "SHLVL", "_",
})


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PROVIDER_PREFLIGHT_INPUT_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError("PROVIDER_PREFLIGHT_INPUT_INVALID")
    return value


def _names(value: Any, reason: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and CALL_NAME.fullmatch(item) for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(reason)
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


def argv_digest(command: list[str]) -> str:
    return canonical_digest([str(item) for item in command])


def execution_environment_digest(environment: dict[str, str] | None = None) -> str:
    source = environment if environment is not None else os.environ
    return canonical_digest({
        str(key): str(value)
        for key, value in sorted(source.items())
        if key not in ENVIRONMENT_EXCLUDES
    })


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def verify(
    contract: dict[str, Any], trace: dict[str, Any], expected_subject: str, *,
    provider: str, adapter_digest: str, canonical_argv_digest: str,
    execution_dependencies: str, execution_environment: str = "",
) -> dict[str, Any]:
    provider = provider.strip().lower()
    if contract.get("schema") != CONTRACT_SCHEMA:
        return {"decision": "block", "reason": "PROVIDER_CONTRACT_SCHEMA_INVALID"}
    if trace.get("schema") != TRACE_SCHEMA or trace.get("offline") is not True:
        return {"decision": "block", "reason": "PROVIDER_TRACE_NOT_OFFLINE"}
    if trace.get("network_calls") != 0:
        return {"decision": "block", "reason": "PROVIDER_TRACE_NETWORK_ACTIVITY"}
    if not provider or contract.get("provider") != provider or trace.get("provider") != provider:
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_PROVIDER_MISMATCH"}
    execution_environment = execution_environment or execution_environment_digest()
    if (
        not expected_subject
        or contract.get("subject_digest") != expected_subject
        or trace.get("subject_digest") != expected_subject
    ):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_SUBJECT_MISMATCH"}
    if (
        not _valid_digest(adapter_digest)
        or not _valid_digest(canonical_argv_digest)
        or not _valid_digest(execution_dependencies)
        or not _valid_digest(execution_environment)
    ):
        return {
            "decision": "block",
            "reason": "PROVIDER_PREFLIGHT_EXECUTION_BINDING_INVALID",
        }
    try:
        required = _names(contract.get("required_calls"), "PROVIDER_REQUIRED_CALLS_INVALID")
        allowed = _names(contract.get("allowed_calls"), "PROVIDER_ALLOWED_CALLS_INVALID")
        observed = _names(trace.get("calls"), "PROVIDER_TRACE_CALLS_INVALID")
    except ValueError as exc:
        return {"decision": "block", "reason": str(exc)}
    if missing_policy := sorted(set(required) - set(allowed)):
        return {
            "decision": "block", "reason": "PROVIDER_REQUIRED_NOT_ALLOWED",
            "calls": missing_policy,
        }
    if missing := sorted(set(required) - set(observed)):
        return {
            "decision": "block", "reason": "PROVIDER_REQUIRED_CALL_MISSING",
            "calls": missing,
        }
    if unexpected := sorted(set(observed) - set(allowed)):
        return {
            "decision": "block", "reason": "PROVIDER_UNEXPECTED_CALL",
            "calls": unexpected,
        }
    repeated = sorted(name for name in set(observed) if observed.count(name) > 1)
    if repeated:
        return {"decision": "block", "reason": "PROVIDER_CALL_REPEATED", "calls": repeated}
    contract_snapshot = {
        "schema": CONTRACT_SCHEMA,
        "subject_digest": expected_subject,
        "provider": provider,
        "required_calls": required,
        "allowed_calls": allowed,
    }
    trace_snapshot = {
        "schema": TRACE_SCHEMA,
        "subject_digest": expected_subject,
        "provider": provider,
        "offline": True,
        "network_calls": 0,
        "calls": observed,
    }
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "decision": "pass",
        "reason": "PROVIDER_OFFLINE_PREFLIGHT_OK",
        "subject_digest": expected_subject,
        "provider": provider,
        "offline": True,
        "network_calls": 0,
        "contract": contract_snapshot,
        "trace": trace_snapshot,
        "contract_digest": canonical_digest(contract_snapshot),
        "trace_digest": canonical_digest(trace_snapshot),
        "adapter_digest": adapter_digest,
        "canonical_argv_digest": canonical_argv_digest,
        "execution_dependency_digest": execution_dependencies,
        "execution_environment_digest": execution_environment,
        "verifier_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "observed_call_counts": {name: observed.count(name) for name in sorted(set(observed))},
        "judge_usage": "unknown",
        "completed_at": now(),
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def execute_preflight(
    contract: dict[str, Any], expected_subject: str, provider: str, adapter: Path,
    command: list[str], *, timeout_seconds: float = 30,
    runner: Callable[..., Any] = run_process_group,
) -> dict[str, Any]:
    adapter = adapter.resolve()
    adapter_digest = _sha256(adapter)
    command = [str(item) for item in command]
    if timeout_seconds <= 0 or adapter_digest == "absent" or not command:
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_EXECUTION_INPUT_INVALID"}
    try:
        executes_adapter = command_executes_path(command, adapter.parent, adapter)
    except GateInputError as exc:
        return {"decision": "block", "reason": exc.reason}
    except (OSError, RuntimeError, ValueError):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_EXECUTION_BINDING_INVALID"}
    if not executes_adapter:
        return {"decision": "block", "reason": "PROVIDER_ADAPTER_ARGV_MISMATCH"}
    try:
        dependencies = execution_dependency_digest(adapter, command)
    except GateInputError as exc:
        return {"decision": "block", "reason": exc.reason}
    except (OSError, RuntimeError, ValueError):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_EXECUTION_BINDING_INVALID"}
    env = {
        **os.environ,
        "HARNESS_PROVIDER_OFFLINE_TRACE": "1",
        "HARNESS_PROVIDER_EXPECTED_SUBJECT": expected_subject,
        "HARNESS_PROVIDER_NAME": provider.strip().lower(),
    }
    try:
        completed = runner(command, cwd=adapter.parent, env=env, timeout=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired):
        return {"decision": "block", "reason": "PROVIDER_OFFLINE_ADAPTER_FAILED"}
    if int(completed.returncode) != 0:
        return {"decision": "block", "reason": "PROVIDER_OFFLINE_ADAPTER_FAILED"}
    if _sha256(adapter) != adapter_digest:
        return {"decision": "block", "reason": "PROVIDER_ADAPTER_CHANGED"}
    try:
        dependencies_after = execution_dependency_digest(adapter, command)
    except GateInputError as exc:
        return {"decision": "block", "reason": exc.reason}
    except (OSError, RuntimeError, ValueError):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_EXECUTION_BINDING_INVALID"}
    if dependencies_after != dependencies:
        return {"decision": "block", "reason": "PROVIDER_EXECUTION_DEPENDENCIES_CHANGED"}
    try:
        trace = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        return {"decision": "block", "reason": "PROVIDER_OFFLINE_TRACE_INVALID"}
    if not isinstance(trace, dict):
        return {"decision": "block", "reason": "PROVIDER_OFFLINE_TRACE_INVALID"}
    return verify(
        contract, trace, expected_subject,
        provider=provider, adapter_digest=adapter_digest,
        canonical_argv_digest=argv_digest(command),
        execution_dependencies=dependencies,
        execution_environment=execution_environment_digest(),
    )


def validate_receipt(
    receipt: dict[str, Any], expected_subject: str, expected_provider: str,
) -> dict[str, str]:
    if set(receipt) != RECEIPT_FIELDS:
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_RECEIPT_FIELDS_INVALID"}
    claimed_digest = str(receipt.get("receipt_digest") or "")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if claimed_digest != canonical_digest(unsigned):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_RECEIPT_DIGEST_INVALID"}
    contract, trace = receipt.get("contract"), receipt.get("trace")
    if not isinstance(contract, dict) or not isinstance(trace, dict):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_SNAPSHOT_MISSING"}
    recomputed = verify(
        contract, trace, expected_subject, provider=expected_provider,
        adapter_digest=str(receipt.get("adapter_digest") or ""),
        canonical_argv_digest=str(receipt.get("canonical_argv_digest") or ""),
        execution_dependencies=str(receipt.get("execution_dependency_digest") or ""),
        execution_environment=str(receipt.get("execution_environment_digest") or ""),
    )
    compared = tuple(RECEIPT_FIELDS - {"completed_at", "receipt_digest"})
    if any(receipt.get(field) != recomputed.get(field) for field in compared):
        return {"decision": "block", "reason": "PROVIDER_PREFLIGHT_RECEIPT_BINDING_INVALID"}
    return {"decision": "pass", "reason": "PROVIDER_PREFLIGHT_RECEIPT_OK"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--expected-subject", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--output", default="")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        result = execute_preflight(
            _load(Path(args.contract)), args.expected_subject, args.provider,
            Path(args.adapter), command, timeout_seconds=args.timeout_seconds,
        )
    except ValueError as exc:
        result = {"decision": "block", "reason": str(exc)}
    if args.output and result.get("decision") == "pass":
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    dump_json(result)
    return 0 if result.get("decision") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
