#!/usr/bin/env python3
"""Commit-bound CI validation for the Harness runtime."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from harness_assurance import finalize
from harness_cache import executed_check, tool_digest
from harness_gates import committed_work_item, run_gate_plan
from harness_output import dump_json
from harness_runtime import (
    apply_code_health, atomic_write_result, canonical_digest, classify_tier, default_result,
    fingerprint, finish_decision, git_changed, mechanical_code_health, now, policy_for,
)
from harness_scope import paths_within_scope
from harness_telemetry import apply_gc_telemetry, apply_usage_receipt, enforce_budget
from harness_task_resolution import valid_task_id


def commit_sha(repo: Path, value: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", value], cwd=repo, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def cmd_ci_check(args: argparse.Namespace) -> int:
    started = time.monotonic()
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    if not valid_task_id(args.task_id):
        dump_json({"decision": "block", "reason": "TASK_ID_INVALID"})
        return 0
    sha = commit_sha(product, args.commit)
    if not sha:
        dump_json({"decision": "block", "reason": "CI_COMMIT_INVALID"})
        return 0
    changed = git_changed(product, sha)
    result = default_result(args.task_id, initial_tier=args.tier)
    result["work_item"] = committed_work_item(harness, product, args.task_id)
    result["task"] = {"task_id": args.task_id, "scope": args.scope, "tier_floor": args.tier}
    result["subject"] = {"kind": "commit", "digest": sha}
    result["policy_digest"] = policy_for(harness, product)
    result["binding_digest"] = canonical_digest(
        {"task_id": args.task_id, "scope": args.scope, "commit": sha}
    )
    result["tier"]["effective"] = classify_tier(changed, floor=args.tier)
    result["invariants"]["task_identity"] = "pass" if args.task_id else "block"
    ci_tools = tool_digest([
        harness / ".harness/scripts/harness_runtime.py",
        harness / ".harness/scripts/harness_scope.py",
        harness / ".harness/scripts/harness_cache.py",
    ])
    tier_fingerprint = fingerprint(
        "tier", sha, result["policy_digest"], changed, args.tier, ci_tools,
    )
    result["checks"]["tier"] = executed_check(
        decision="pass", fingerprint=tier_fingerprint, subject_digest=sha,
        policy_digest=result["policy_digest"], completed_at=now(),
        effective_tier=result["tier"]["effective"],
    )
    scope_fingerprint = fingerprint(
        "scope", sha, result["policy_digest"], changed, sorted(args.scope), ci_tools,
    )
    result["checks"]["scope"] = executed_check(
        decision="pass" if args.scope and paths_within_scope(changed, args.scope) else "block",
        fingerprint=scope_fingerprint, subject_digest=sha,
        policy_digest=result["policy_digest"], completed_at=now(),
    )
    result["invariants"]["scope"] = result["checks"]["scope"]["decision"]
    mechanical = mechanical_code_health(product, changed, tier=result["tier"]["effective"])
    mechanical["subject_digest"] = sha
    mechanical["fingerprint"] = fingerprint(
        "code_health", sha, result["policy_digest"], changed, result["tier"]["effective"],
    )
    gc_result = None
    if args.gc_result:
        try:
            gc_result = json.loads(Path(args.gc_result).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    if mechanical.get("agent_required") and gc_result:
        apply_gc_telemetry(result, gc_result)
    apply_code_health(
        result, mechanical, gc_result=gc_result, subject_digest=sha,
        policy_digest=result["policy_digest"],
    )
    gates = run_gate_plan(
        harness, product, tier=result["tier"]["effective"], subject_digest=sha,
        policy_digest=result["policy_digest"], ci_task_id=args.task_id,
        changed_files=changed, read_only=True,
    )
    result["checks"].update(gates["checks"])
    if gates["missing"]:
        result["blockers"].append("REQUIRED_GATE_MISSING")
    result["invariants"]["risk_validation"] = (
        "pass" if all(check["decision"] == "pass" for check in result["checks"].values())
        and not gates["missing"] else "block"
    )
    result["invariants"]["final_result"] = "pass"
    result["cost"]["harness"]["gate_duration_ms"] += int((time.monotonic() - started) * 1000)
    apply_usage_receipt(
        result, task_id=args.task_id, subject_digest=sha, policy_digest=result["policy_digest"],
    )
    enforce_budget(result)
    finalize(result, finish_decision, local=True)
    if args.output:
        atomic_write_result(Path(args.output), result)
    dump_json({"decision": result["decision"], "reason": "CI_SHADOW_RESULT", "result": result})
    return 0
