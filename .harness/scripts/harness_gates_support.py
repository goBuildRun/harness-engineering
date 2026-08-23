"""Small gate execution helpers kept outside the tier planner."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from harness_runtime import canonical_digest, now
from process_control import run_process_group


def payload(output: str, returncode: int) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return {"decision": "block", "reason": "GATE_OUTPUT_INVALID"}
    if not isinstance(value, dict) or value.get("decision") not in {"pass", "block"}:
        return {"decision": "block", "reason": "GATE_OUTPUT_INVALID"}
    if returncode != 0:
        value["decision"] = "block"
        value["reason"] = f"GATE_EXIT_{returncode}: {value.get('reason', '')}"
    return value


def gate_timeout_seconds(default: int = 120) -> int:
    raw = os.environ.get("HARNESS_GATE_TIMEOUT_SECONDS", "")
    try:
        return max(1, int(raw or default))
    except ValueError:
        return default


def not_executed_check(
    name: str, subject_digest: str, policy_digest: str, reason: str, timeout_kind: str,
) -> dict[str, Any]:
    input_digest = canonical_digest({"gate": name, "not_executed": reason})
    return {
        "decision": "block", "source": "not_executed",
        "subject_digest": subject_digest, "policy_digest": policy_digest,
        "input_digest": input_digest,
        "fingerprint": canonical_digest({"gate": name, "input": input_digest}),
        "started_at": now(), "completed_at": now(),
        "duration_ms": 0, "tool_wait_ms": 0, "attempt": 0,
        "retry_limit": 1, "budget_ms": 0,
        "timeout_kind": timeout_kind, "reason": reason,
    }


def blocked_gate_plan(
    required_gates: list[str], subject_digest: str, policy_digest: str,
    reason: str, plan_started: float, clock: Callable[[], float],
) -> dict[str, Any]:
    checks = {
        name: not_executed_check(name, subject_digest, policy_digest, reason, "input")
        for name in required_gates
    }
    return {
        "decision": "block", "checks": checks, "missing": [], "cache_hits": 0,
        "wall_duration_ms": int((clock() - plan_started) * 1000), "reason": reason,
    }


def run_gate_command(command: list[str], *, gate: str, cwd: Path,
                     env: dict[str, str], timeout: float,
                     process_runner: Callable[..., Any] = run_process_group,
                     ) -> tuple[dict[str, Any], int]:
    try:
        completed = process_runner(command, cwd=cwd, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        log = " ".join(str(output).splitlines()[-25:])[:2000]
        reason = f"GATE_TIMEOUT: gate={gate}; timeout={timeout}s"
        if log:
            reason += f"; log={log}"
        return {"decision": "block", "reason": reason}, 124
    return payload(completed.stdout, completed.returncode), completed.returncode
