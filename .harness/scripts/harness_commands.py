#!/usr/bin/env python3
"""Command handlers for the lean Harness runtime."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from harness_output import dump_json
from harness_enforcement import (evaluate_enforcement, git_identity, load_snapshot,
                                 probe_live, save_snapshot)
from harness_assurance import audit_report, finalize, refresh_result
from harness_cache import executed_check, reuse_check, tool_digest
from harness_gates import committed_work_item, run_gate_plan
from harness_telemetry import apply_gc_telemetry, apply_usage_receipt, enforce_budget
from harness_runtime import (
    active_task_path, apply_code_health, atomic_write_result,
    canonical_digest, classify_tier, default_result, task_kind_tier,
    fingerprint, finish_decision, git_changed, invoke_gc_once, load_result,
    mechanical_code_health, now, policy_for, resolve_task_id, result_path,
    subject_for, workspace_root,
)
from harness_scope import paths_within_scope
from harness_state import invalidate_if_stale
from product_context import product_config
from worktree_baseline import capture_baseline, changed_since_baseline

def commit_sha(repo: Path, value: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", value], cwd=repo, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def cmd_start(args: argparse.Namespace) -> int:
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    task_id = args.task_id or f"task-{uuid.uuid4().hex[:12]}"
    path = result_path(product, task_id)
    if path.exists():
        dump_json({"decision": "pass", "reason": "TASK_RESUMED", "result": load_result(path)})
        return 0
    change_reason = str(getattr(args, "reason", "") or "").strip()
    if args.kind in {"scope-change", "hotfix"} and not change_reason:
        dump_json({"decision": "block", "reason": "TASK_KIND_REASON_REQUIRED", "kind": args.kind})
        return 0
    initial = task_kind_tier(args.kind, args.tier or "standard")
    result = default_result(task_id, initial_tier=initial,
                            work_item={"id": args.work_item} if args.work_item else None)
    baseline = path.parent / "worktree_baseline.json"
    capture_baseline(product, baseline, work_item_id=task_id)
    result["baseline"] = {"digest": canonical_digest(json.loads(baseline.read_text())), "source": "start"}
    result["policy_digest"] = policy_for(harness, product)
    binding = {
        "task_id": task_id, "scope": args.scope, "tier_floor": initial,
        "work_item": args.work_item or None, "kind": args.kind,
        "change_reason": change_reason or None,
        "primary_role": "gc-sweeper" if args.kind == "debt-maintenance" else "lead-agent",
    }
    result["binding_digest"] = canonical_digest(binding)
    result["invariants"]["task_identity"] = "pass"
    result["task"] = binding
    if initial == "lite":
        binding_path = workspace_root(product) / "planning" / "tasks" / task_id / "task.json"
        binding_path.parent.mkdir(parents=True, exist_ok=True)
        binding_path.write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_write_result(path, result)
    active = active_task_path(product)
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(json.dumps({"task_id": task_id, "activated_at": now()}, indent=2) + "\n")
    dump_json({"decision": "pass", "reason": "TASK_STARTED", "result": result})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    product = Path(args.product_root).resolve()
    task_id, candidates = resolve_task_id(product, args.task_id)
    if not task_id:
        dump_json({"decision": "block", "reason": "TASK_INFERENCE_AMBIGUOUS", "candidates": candidates})
        return 0
    path = result_path(product, task_id)
    if not path.is_file():
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    result = load_result(path)
    baseline = path.parent / "worktree_baseline.json"
    changed = changed_since_baseline(product, baseline) if baseline.is_file() else git_changed(product)
    current_subject = subject_for(product, changed)
    current_policy = policy_for(Path(args.harness_root).resolve(), product)
    if invalidate_if_stale(result, current_subject, current_policy):
        atomic_write_result(path, result)
    config = product_config(product).get("enforcement") or {}
    discovered_repository, _ = git_identity(product)
    repository = str(config.get("repository") or discovered_repository)
    branch = os.environ.get("HARNESS_TARGET_BRANCH", str(config.get("target_branch") or "main"))
    required_check = os.environ.get(
        "HARNESS_REQUIRED_CHECK", str(config.get("required_check") or "harness-commit-acceptance")
    )
    enforcement = evaluate_enforcement(
        load_snapshot(product), repository=repository, branch=branch,
        required_check=required_check,
    )
    refresh_result(result, product, enforcement)
    atomic_write_result(path, result)
    dump_json({"decision": "pass", "reason": "TASK_STATUS", "result": result})
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    started = time.monotonic()
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    task_id, candidates = resolve_task_id(product, args.task_id)
    if not task_id:
        dump_json({"decision": "block", "reason": "TASK_INFERENCE_AMBIGUOUS", "candidates": candidates})
        return 0
    path = result_path(product, task_id)
    if not path.is_file():
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    result = load_result(path)
    previous_checks = deepcopy(result.get("checks", {}))
    had_checks = bool(result.get("checks"))
    result["blockers"] = []
    if had_checks and result.get("state") != "active":
        result["cost"]["harness"]["reruns"] = int(
            result["cost"]["harness"].get("reruns") or 0
        ) + 1
    task_root, baseline = path.parent, path.parent / "worktree_baseline.json"
    changed = changed_since_baseline(product, baseline) if baseline.is_file() else git_changed(product)
    tier_floor = result["tier"]["effective"]
    subject = subject_for(product, changed)
    current_policy = policy_for(harness, product)
    invalidate_if_stale(result, subject, current_policy)
    result["subject"] = {"kind": "worktree", "digest": subject}
    result["policy_digest"] = current_policy
    result["blockers"] = [
        blocker for blocker in result.get("blockers", []) if blocker != "INPUT_CHANGED"
    ]
    tools = tool_digest([
        harness / ".harness/scripts/harness_runtime.py",
        harness / ".harness/scripts/harness_scope.py",
        harness / ".harness/scripts/harness_cache.py",
    ])
    tier_fingerprint = fingerprint(
        "tier", subject, current_policy, changed, tier_floor, tools,
    )
    tier_check = reuse_check(
        previous_checks.get("tier"), gate="tier", fingerprint=tier_fingerprint,
        subject_digest=subject, policy_digest=current_policy,
    )
    if tier_check:
        effective = tier_check["effective_tier"]
        result["cost"]["harness"]["cache_hits"] = int(
            result["cost"]["harness"].get("cache_hits") or 0
        ) + 1
    else:
        effective = classify_tier(changed, floor=tier_floor)
        tier_check = executed_check(
            decision="pass", fingerprint=tier_fingerprint,
            subject_digest=subject, policy_digest=current_policy,
            completed_at=now(), effective_tier=effective,
        )
    result["tier"]["effective"] = effective
    result["checks"]["tier"] = tier_check
    declared = set(result.get("task", {}).get("scope") or [])
    result["invariants"]["task_identity"] = "pass" if result.get("task_id") else "block"
    scope_fingerprint = fingerprint(
        "scope", subject, current_policy, changed, sorted(declared), tools,
    )
    scope_check = reuse_check(
        previous_checks.get("scope"), gate="scope", fingerprint=scope_fingerprint,
        subject_digest=subject, policy_digest=current_policy,
    )
    if scope_check:
        result["cost"]["harness"]["cache_hits"] = int(
            result["cost"]["harness"].get("cache_hits") or 0
        ) + 1
    else:
        scope_pass = bool(declared) and paths_within_scope(changed, declared)
        scope_check = executed_check(
            decision="pass" if scope_pass else "block",
            fingerprint=scope_fingerprint, subject_digest=subject,
            policy_digest=current_policy, completed_at=now(),
        )
    result["checks"]["scope"] = scope_check
    result["invariants"]["scope"] = scope_check["decision"]
    mechanical = mechanical_code_health(product, changed, tier=effective)
    mechanical["fingerprint"] = fingerprint("code_health", subject, result["policy_digest"], changed, effective)
    gc_result = invoke_gc_once(result, task_root, mechanical, changed, product) if mechanical["agent_required"] else None
    if gc_result:
        changed_after = changed_since_baseline(product, baseline) if baseline.is_file() else git_changed(product)
        subject_after = subject_for(product, changed_after)
        if subject_after != subject:
            changed, subject = changed_after, subject_after
            effective = classify_tier(changed, floor=effective)
            result["tier"]["effective"] = effective
            result["subject"] = {"kind": "worktree", "digest": subject}
            tier_fingerprint = fingerprint(
                "tier", subject, current_policy, changed, tier_floor, tools,
            )
            result["checks"]["tier"] = executed_check(
                decision="pass", fingerprint=tier_fingerprint,
                subject_digest=subject, policy_digest=current_policy,
                completed_at=now(), effective_tier=effective,
            )
            scope_pass = bool(declared) and paths_within_scope(changed, declared)
            scope_fingerprint = fingerprint(
                "scope", subject, current_policy, changed, sorted(declared), tools,
            )
            result["checks"]["scope"] = executed_check(
                decision="pass" if scope_pass else "block",
                fingerprint=scope_fingerprint, subject_digest=subject,
                policy_digest=current_policy, completed_at=now(),
            )
            result["invariants"]["scope"] = result["checks"]["scope"]["decision"]
            mechanical = mechanical_code_health(product, changed, tier=effective)
            mechanical["fingerprint"] = fingerprint(
                "code_health", subject, result["policy_digest"], changed, effective
            )
            result["checks"].pop("tests", None)
            result["checks"].pop("structure", None)
    apply_code_health(result, mechanical, gc_result=gc_result, subject_digest=subject,
                      policy_digest=result["policy_digest"])
    if not args.skip_legacy_gates:
        gates = run_gate_plan(
            harness, product, tier=effective, subject_digest=subject,
            policy_digest=result["policy_digest"], changed_files=changed,
        )
        result["checks"].update(gates["checks"])
        if gates["missing"]:
            result["blockers"].append("REQUIRED_GATE_MISSING")
    result["invariants"]["risk_validation"] = "pass" if all(
        check.get("decision") == "pass" and not check.get("stale")
        for check in result["checks"].values()
    ) else "block"
    result["invariants"]["final_result"] = "pass"
    result["cost"]["harness"]["gate_duration_ms"] += int((time.monotonic() - started) * 1000)
    apply_usage_receipt(result, task_id=task_id, subject_digest=subject,
                        policy_digest=result["policy_digest"])
    enforce_budget(result)
    finalize(result, finish_decision)
    atomic_write_result(path, result)
    reason = result["blockers"][0] if result["blockers"] else "FINISH_OK"
    dump_json({"decision": result["decision"], "reason": reason, "result": result})
    return 0


def cmd_ci_check(args: argparse.Namespace) -> int:
    started = time.monotonic()
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    sha = commit_sha(product, args.commit)
    if not sha:
        dump_json({"decision": "block", "reason": "CI_COMMIT_INVALID"})
        return 0
    changed = git_changed(product, sha)
    result = default_result(args.task_id, initial_tier=args.tier)
    result["work_item"] = committed_work_item(harness, product, args.task_id)
    result["subject"] = {"kind": "commit", "digest": sha}
    result["policy_digest"] = policy_for(harness, product)
    result["binding_digest"] = canonical_digest({"task_id": args.task_id, "scope": args.scope, "commit": sha})
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
    scope_pass = bool(args.scope) and paths_within_scope(changed, args.scope)
    scope_fingerprint = fingerprint(
        "scope", sha, result["policy_digest"], changed, sorted(args.scope), ci_tools,
    )
    result["checks"]["scope"] = executed_check(
        decision="pass" if scope_pass else "block", fingerprint=scope_fingerprint,
        subject_digest=sha, policy_digest=result["policy_digest"], completed_at=now(),
    )
    result["invariants"]["scope"] = result["checks"]["scope"]["decision"]
    mechanical = mechanical_code_health(product, changed, tier=result["tier"]["effective"])
    mechanical["subject_digest"] = sha
    mechanical["fingerprint"] = fingerprint(
        "code_health", sha, result["policy_digest"], changed, result["tier"]["effective"]
    )
    gc_result = None
    if args.gc_result:
        try:
            gc_result = json.loads(Path(args.gc_result).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    if mechanical.get("agent_required") and gc_result:
        apply_gc_telemetry(result, gc_result)
    apply_code_health(result, mechanical, gc_result=gc_result, subject_digest=sha,
                      policy_digest=result["policy_digest"])
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
    apply_usage_receipt(result, task_id=args.task_id, subject_digest=sha,
                        policy_digest=result["policy_digest"])
    enforce_budget(result)
    finalize(result, finish_decision, local=True)
    if args.output:
        atomic_write_result(Path(args.output), result)
    dump_json({"decision": result["decision"], "reason": "CI_SHADOW_RESULT", "result": result})
    return 0

def cmd_enforcement(args: argparse.Namespace) -> int:
    product = Path(args.product_root).resolve()
    config = product_config(product).get("enforcement") or {}
    discovered_repository, current_branch = git_identity(product)
    repository = args.repository or str(config.get("repository") or discovered_repository)
    branch = args.branch or os.environ.get(
        "HARNESS_TARGET_BRANCH", str(config.get("target_branch") or "main")
    ) or current_branch
    required_check = args.required_check or str(
        config.get("required_check") or "harness-commit-acceptance"
    )
    if args.snapshot:
        try:
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            dump_json({"decision": "block", "reason": "ENFORCEMENT_SNAPSHOT_INVALID"})
            return 0
        snapshot["source"] = "snapshot"
    else:
        try:
            snapshot = probe_live(
                product, authority_url=args.authority_url,
                credentials={"authority": os.environ.get("HARNESS_AUTHORITY_TOKEN", ""),
                             "github": os.environ.get("GITHUB_TOKEN", "")},
                repository=repository,
                branch=branch, required_check=required_check,
            )
        except Exception as exc:
            dump_json({"decision": "block", "reason": f"ENFORCEMENT_LIVE_PROBE_FAILED: {exc}"})
            return 0
    save_snapshot(product, snapshot)
    evaluated = evaluate_enforcement(
        snapshot, repository=repository, branch=branch, required_check=required_check,
    )
    dump_json({
        "decision": "pass" if evaluated["enforcement"] == "enforced" else "block",
        "reason": evaluated["notice"], "enforcement": evaluated,
        "assurance": audit_report(product, evaluated),
    })
    return 0
