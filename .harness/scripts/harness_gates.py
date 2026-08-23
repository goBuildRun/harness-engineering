#!/usr/bin/env python3
"""Tier-aware structured adapters for existing Harness gates."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from harness_runtime import canonical_digest, now
from harness_output import dump_json
from process_control import run_process_group
from workspace_paths import active_planning_gate_path, ci_planning_gate_path, load_layout
from harness_gate_execution import (
    CandidateManifest, GateInputError, build_spec, execute_specs, gate_input_digest,
)
from harness_gate_work_items import (
    committed_work_item,  # noqa: F401 - compatibility re-export
    committed_work_item_resolution,  # noqa: F401 - compatibility re-export
    prepare_ci_task,
    validate_planning_credential,
)
from harness_strict_gate import validate as _strict_evidence


TIER_GATES = {
    "lite": ("harness", "structure", "diff_integrity", "quality_lint", "quality_test"),
    "standard": (
        "planning", "harness", "structure", "diff_integrity", "plan_sync", "dag_sync",
        "qa_evidence", "knowledge", "growth_release",
        "quality_lint", "quality_test",
    ),
    "strict": (
        "planning", "harness", "structure", "diff_integrity", "plan_sync", "dag_sync",
        "qa_evidence", "knowledge", "growth_release",
        "quality_lint", "quality_test", "strict_evidence",
    ),
}
DEFAULT_GATE_TIMEOUT_SECONDS = 120


def checks_for_tier(checks: dict[str, dict[str, Any]], tier: str) -> dict[str, dict[str, Any]]:
    required = {"tier", "scope", "code_health", *TIER_GATES[tier]}
    return {name: check for name, check in checks.items() if name in required}

FRONTEND_PARTS = {"frontend", "web", "ui", "app", "pages", "components"}
FRONTEND_SUFFIXES = {".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss"}


def browser_required(changed_files: list[str]) -> bool:
    return any(
        Path(path).suffix.lower() in FRONTEND_SUFFIXES
        or bool(set(Path(path).parts) & FRONTEND_PARTS)
        for path in changed_files
    )


def _commands(harness: Path) -> dict[str, list[str]]:
    scripts = harness / ".harness/scripts"
    return {
        "harness": ["bash", str(scripts / "validate_harness.sh")],
        "structure": ["bash", str(scripts / "structure_guard.sh"), "--diff"],
        "diff_integrity": [
            "python3", str(scripts / "diff_integrity_check.py"),
            "--harness-root", str(harness),
        ],
        "plan_sync": ["bash", str(scripts / "plan_sync_check.sh")],
        "dag_sync": ["bash", str(scripts / "dag_sync_check.sh")],
        "qa_evidence": ["bash", str(scripts / "qa_evidence_check.sh")],
        "knowledge": ["bash", str(scripts / "harness_knowledge.sh"), "check-planning"],
        "growth_review": ["bash", str(scripts / "harness_growth.sh"), "review-status"],
        "growth_freshness": ["bash", str(scripts / "harness_growth.sh"), "freshness"],
        "growth_release": [
            "python3", str(scripts / "harness_growth_release.py"),
            "--harness-root", str(harness), "--product-root", "__PRODUCT_ROOT__",
        ],
        "quality_lint": ["bash", str(scripts / "quality_commands.sh"), "lint"],
        "quality_test": ["bash", str(scripts / "quality_commands.sh"), "test"],
    }


def _payload(output: str, returncode: int) -> dict[str, Any]:
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


def _gate_timeout_seconds() -> int:
    raw = os.environ.get("HARNESS_GATE_TIMEOUT_SECONDS", "")
    try:
        return max(1, int(raw or DEFAULT_GATE_TIMEOUT_SECONDS))
    except ValueError:
        return DEFAULT_GATE_TIMEOUT_SECONDS


def _not_executed_check(
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


def _blocked_gate_plan(
    required_gates: list[str], subject_digest: str, policy_digest: str,
    reason: str, plan_started: float, clock: Callable[[], float],
) -> dict[str, Any]:
    checks = {
        name: _not_executed_check(name, subject_digest, policy_digest, reason, "input")
        for name in required_gates
    }
    return {
        "decision": "block", "checks": checks, "missing": [], "cache_hits": 0,
        "wall_duration_ms": int((clock() - plan_started) * 1000), "reason": reason,
    }


def _run_gate_command(command: list[str], *, gate: str, cwd: Path,
                      env: dict[str, str], timeout: float) -> tuple[dict[str, Any], int]:
    try:
        completed = run_process_group(command, cwd=cwd, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        log = " ".join(str(output).splitlines()[-25:])[:2000]
        reason = f"GATE_TIMEOUT: gate={gate}; timeout={timeout}s"
        if log:
            reason += f"; log={log}"
        return {"decision": "block", "reason": reason}, 124
    return _payload(completed.stdout, completed.returncode), completed.returncode


def run_gate_plan(harness: Path, product: Path, *, tier: str,
                  subject_digest: str, policy_digest: str,
                  ci_task_id: str = "", changed_files: list[str] | None = None,
                  read_only: bool = False,
                  previous_checks: dict[str, dict[str, Any]] | None = None,
                  remaining_budget_ms: int | None = None,
                  ledger: Path | None = None,
                  task_id: str = "", work_item_id: str = "",
                  work_item_provider: str = "",
                  clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    plan_started = clock()
    deadline = (
        plan_started + max(0, remaining_budget_ms) / 1000
        if remaining_budget_ms is not None else None
    )
    required_gates = list(TIER_GATES[tier])
    if tier == "standard" and browser_required(changed_files or []):
        required_gates.append("browser_qa")
    if ci_task_id and tier != "lite":
        if not prepare_ci_task(
            harness, product, ci_task_id, changed_files=changed_files or [],
        ):
            return _blocked_gate_plan(
                required_gates, subject_digest, policy_digest,
                "CI_PLANNING_CREDENTIAL_INVALID", plan_started, clock,
            )
    layout = load_layout(harness, product)
    planning_gate = (
        ci_planning_gate_path(layout, ci_task_id)
        if ci_task_id else active_planning_gate_path(layout)
    )
    try:
        planning_credential = json.loads(planning_gate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        planning_credential = {}
    bound_task_id = ci_task_id or task_id
    if bound_task_id and tier != "lite":
        credential_binding = validate_planning_credential(
            harness, product, bound_task_id, planning_credential,
            work_item_id=work_item_id, provider=work_item_provider,
            require_ci_task_id=bool(ci_task_id),
            changed_files=changed_files if ci_task_id else None,
        )
        if credential_binding["decision"] == "block":
            return _blocked_gate_plan(
                required_gates, subject_digest, policy_digest,
                credential_binding["reason"], plan_started, clock,
            )
    commands = _commands(harness)
    if "growth_release" in commands:
        commands["growth_release"] = [
            str(product) if item == "__PRODUCT_ROOT__" else item
            for item in commands["growth_release"]
        ]
    env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product)}
    if ci_task_id:
        env.update({
            "HARNESS_CI_TASK_ID": ci_task_id,
            "HARNESS_CI_PLANNING_GATE": str(planning_gate),
            "HARNESS_CI_CHANGED_FILES_DIGEST": canonical_digest(changed_files or []),
        })
    if read_only:
        env.update({"CI": "true", "HARNESS_GATE_READ_ONLY": "1"})
    timeout = _gate_timeout_seconds()
    if deadline is not None and clock() >= deadline:
        checks = {
            name: _not_executed_check(
                name, subject_digest, policy_digest, "STORY_BUDGET_EXCEEDED", "story",
            ) for name in required_gates
        }
        return {
            "decision": "block", "checks": checks, "missing": [], "cache_hits": 0,
            "wall_duration_ms": int((clock() - plan_started) * 1000),
            "reason": "STORY_BUDGET_EXCEEDED",
        }

    planning_check = None
    if "planning" in required_gates:
        planning_pass = planning_credential.get("decision") == "pass"
        input_digest = canonical_digest({
            "planning_gate": str(planning_gate), "content": planning_credential,
        })
        planning_check = {
            "decision": "pass" if planning_pass else "block", "source": "executed",
            "subject_digest": subject_digest, "policy_digest": policy_digest,
            "input_digest": input_digest,
            "fingerprint": canonical_digest({"gate": "planning", "input": input_digest}),
            "started_at": now(), "completed_at": now(), "duration_ms": 0,
            "tool_wait_ms": 0, "attempt": 1, "retry_limit": 1, "budget_ms": 0,
            "timeout_kind": "none",
            "reason": "PLANNING_CREDENTIAL_OK" if planning_pass else "NO_PLANNING_GATE",
        }
        if not planning_pass:
            return {
                "decision": "block", "checks": {"planning": planning_check},
                "missing": sorted(set(required_gates) - {"planning"}), "cache_hits": 0,
                "wall_duration_ms": int((clock() - plan_started) * 1000),
                "reason": "NO_PLANNING_GATE",
            }

    commands_to_run: dict[str, list[str]] = {}
    for name in required_gates:
        if name in {"planning", "strict_evidence"}:
            continue
        if name == "browser_qa":
            url = os.environ.get("HARNESS_BROWSER_QA_URL", "")
            commands_to_run[name] = ([
                "python3", str(harness / ".harness/scripts/browser_qa.py"), url,
                "--action", "audit", "--harness-root", str(harness),
                "--product-root", str(product),
            ] if url else ["browser_qa", "missing-url"])
            continue
        command = list(commands[name])
        if name == "diff_integrity" and read_only:
            command.extend(["--commit", subject_digest])
        commands_to_run[name] = command

    specs = []
    candidates_manifest = CandidateManifest(
        harness, product, deadline=deadline, clock=clock,
    )
    try:
        for name, command in commands_to_run.items():
            input_digest = gate_input_digest(
                name, harness=harness, product=product, changed_files=changed_files or [],
                planning_gate=planning_gate, planning_credential=planning_credential,
                command=command, task_id=task_id, work_item_id=work_item_id,
                candidates_manifest=candidates_manifest, deadline=deadline,
            )
            current_remaining_ms = (
                max(0, int((deadline - clock()) * 1000)) if deadline is not None else None
            )
            specs.append(build_spec(
                name, command, input_digest=input_digest, tier=tier,
                configured_timeout=timeout, remaining_budget_ms=current_remaining_ms,
                cache_safe=candidates_manifest.gate_cache_safe(name),
            ))
    except GateInputError as exc:
        timeout_kind = "stage" if exc.reason == "STAGE_BUDGET_EXCEEDED" else "input"
        checks = {
            name: (
                planning_check if name == "planning" and planning_check is not None
                else _not_executed_check(
                    name, subject_digest, policy_digest, exc.reason, timeout_kind,
                )
            ) for name in required_gates
        }
        return {
            "decision": "block", "checks": checks, "missing": [], "cache_hits": 0,
            "wall_duration_ms": int((clock() - plan_started) * 1000),
            "reason": exc.reason,
        }

    def runner(command: list[str], name: str, gate_timeout: float) -> tuple[dict[str, Any], int]:
        if name == "browser_qa" and command[:2] == ["browser_qa", "missing-url"]:
            return {"decision": "block", "reason": "BROWSER_QA_CONFIG_REQUIRED"}, 0
        return _run_gate_command(
            command, gate=name, cwd=harness, env=env, timeout=gate_timeout,
        )

    executed, cache_hits = execute_specs(
        specs, runner=runner, previous_checks=previous_checks or {},
        subject_digest=subject_digest, policy_digest=policy_digest,
        ledger=ledger, task_id=task_id, work_item_id=work_item_id,
        deadline=deadline, clock=clock, candidates_manifest=candidates_manifest,
    )
    available = dict(executed)
    if planning_check is not None:
        available["planning"] = planning_check

    if "strict_evidence" in required_gates:
        work_item = planning_credential.get("work_item") or {}
        production_policy = (
            work_item.get("production_evidence") if isinstance(work_item, dict) else None
        )
        expected_provider = (
            str(work_item.get("provider") or "") if isinstance(work_item, dict) else ""
        )
        strict_started = clock()
        if deadline is not None and strict_started >= deadline:
            available["strict_evidence"] = _not_executed_check(
                "strict_evidence", subject_digest, policy_digest,
                "STAGE_BUDGET_EXCEEDED", "stage",
            )
        else:
            try:
                payload = _strict_evidence(
                    subject_digest, production_policy, product, expected_provider,
                )
            except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
                payload = {"decision": "block", "reason": "STRICT_EVIDENCE_INVALID"}
            if deadline is not None and clock() >= deadline:
                payload = {"decision": "block", "reason": "STAGE_BUDGET_EXCEEDED"}
            input_digest = canonical_digest({
                "subject": subject_digest, "production_policy": production_policy,
                "receipt_path_digest": canonical_digest(
                    os.environ.get("HARNESS_STRICT_EVIDENCE", ""),
                ),
            })
            stage_timeout = payload.get("reason") == "STAGE_BUDGET_EXCEEDED"
            available["strict_evidence"] = {
                "decision": payload["decision"], "source": "executed",
                "subject_digest": subject_digest, "policy_digest": policy_digest,
                "input_digest": input_digest,
                "fingerprint": canonical_digest({"gate": "strict_evidence", "input": input_digest}),
                "started_at": now(), "completed_at": now(),
                "duration_ms": max(0, int((clock() - strict_started) * 1000)),
                "tool_wait_ms": 0, "attempt": 1, "retry_limit": 1, "budget_ms": 0,
                "timeout_kind": "stage" if stage_timeout else "none",
                "reason": str(payload.get("reason") or ""),
            }

    checks = {name: available[name] for name in required_gates if name in available}
    required = set(required_gates)
    missing = sorted(required - set(checks))
    decision = "pass" if not missing and all(
        check["decision"] == "pass" for check in checks.values()
    ) else "block"
    return {
        "decision": decision, "checks": checks, "missing": missing,
        "cache_hits": cache_hits,
        "wall_duration_ms": int((clock() - plan_started) * 1000),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--product-root", default=os.environ.get("HARNESS_PRODUCT_ROOT", os.getcwd()))
    parser.add_argument("--tier", choices=tuple(TIER_GATES), default="standard")
    parser.add_argument("--subject", default="compat-check")
    parser.add_argument("--policy", default="compat-check")
    args = parser.parse_args()
    result = run_gate_plan(
        Path(args.harness_root).resolve(), Path(args.product_root).resolve(),
        tier=args.tier, subject_digest=args.subject, policy_digest=args.policy,
    )
    dump_json(result)
    return 0 if result["decision"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
