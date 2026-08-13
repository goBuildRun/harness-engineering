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
from harness_assurance import finalize, refresh_result
from harness_attestation import verify_attestation
from harness_cache import executed_check, reuse_check, tool_digest
from harness_gates import checks_for_tier, committed_work_item, run_gate_plan
from harness_telemetry import apply_automatic_usage, apply_gc_telemetry, apply_usage_receipt, enforce_budget
from codex_usage_receipt import automatic_receipt
from harness_usage_ledger import apply_story_usage, capture_usage_baseline
from harness_runtime import (
    active_task_path, apply_code_health, atomic_write_result,
    canonical_digest, classify_tier, default_result, task_kind_tier,
    fingerprint, finish_decision, git_changed, invoke_gc_once, load_result,
    mechanical_code_health, now, policy_for, resolve_task_id, result_path,
    subject_for, workspace_root,
)
from harness_scope import paths_within_scope
from harness_state import invalidate_if_stale
from worktree_baseline import capture_baseline, changed_since_baseline

def commit_sha(repo: Path, value: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", value], cwd=repo, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def execution_paths(product: Path, task_id: str, changed: list[str]) -> list[str]:
    generated = workspace_root(product) / "planning" / "tasks" / task_id / "task.json"
    try:
        generated_rel = str(generated.relative_to(product))
    except ValueError:
        generated_rel = ""
    runs = str((workspace_root(product) / "runs").relative_to(product)).rstrip("/") + "/"
    return [path for path in changed if path != generated_rel and not path.startswith(runs)]


def refresh_assurance(result: dict, product: Path, policy_digest: str, *, phase: str) -> None:
    attestation = verify_attestation(product, commit="HEAD", policy_digest=policy_digest)
    refresh_result(result, product, policy_digest, phase=phase, verified_at=now(),
                   attestation=attestation)


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
    capture_usage_baseline(
        result, automatic_receipt(task_id, result["subject"]["digest"], result["policy_digest"]),
    )
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


def cmd_amend(args: argparse.Namespace) -> int:
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    path = result_path(product, args.task_id)
    if not path.is_file():
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    reason = str(args.reason or "").strip()
    scope = sorted({str(item).strip().rstrip("/") for item in args.scope if str(item).strip()})
    if not reason:
        dump_json({"decision": "block", "reason": "TASK_AMEND_REASON_REQUIRED"})
        return 0
    if not scope or any(Path(item).is_absolute() or ".." in Path(item).parts for item in scope):
        dump_json({"decision": "block", "reason": "TASK_AMEND_SCOPE_INVALID"})
        return 0

    result = load_result(path)
    previous = dict(result.get("task") or {})
    binding = {
        **previous,
        "task_id": args.task_id,
        "scope": scope,
        "tier_floor": result["tier"]["initial"],
        "work_item": (result.get("work_item") or {}).get("id"),
        "source": previous.get("source") or result.get("baseline", {}).get("source") or "amendment",
    }
    if getattr(args, "kind", ""):
        binding["kind"] = args.kind
    revisions = list(result.get("binding_revisions") or [])
    revisions.append({
        "amended_at": now(), "reason": reason,
        "previous_binding_digest": result.get("binding_digest") or "",
        "previous_scope": list(previous.get("scope") or []), "scope": scope,
        "previous_kind": previous.get("kind") or "", "kind": binding.get("kind") or "",
    })
    result["task"] = binding
    result["binding_revisions"] = revisions
    result["binding_digest"] = canonical_digest(binding)
    result["policy_digest"] = policy_for(harness, product)
    result["tier"]["effective"] = result["tier"]["initial"]
    result["state"] = "active"
    result["decision"] = "block"
    result["blockers"] = sorted(set(result.get("blockers") or []) | {"TASK_BINDING_CHANGED"})
    result["invariants"]["scope"] = "pending"
    result["invariants"]["risk_validation"] = "pending"
    result["invariants"]["final_result"] = "pending"
    for check in result.get("checks", {}).values():
        check["stale"] = True
    atomic_write_result(path, result)
    dump_json({"decision": "pass", "reason": "TASK_BINDING_AMENDED", "result": result})
    return 0


def cmd_usage_baseline(args: argparse.Namespace) -> int:
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    path = result_path(product, args.task_id)
    reason = str(args.reason or "").strip()
    if not path.is_file():
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    if not reason:
        dump_json({"decision": "block", "reason": "USAGE_BASELINE_REASON_REQUIRED"})
        return 0
    result = load_result(path)
    changed = changed_since_baseline(product, path.parent / "worktree_baseline.json")
    subject = subject_for(product, changed)
    policy = policy_for(harness, product)
    receipt = automatic_receipt(args.task_id, subject, policy)
    if receipt is None:
        dump_json({"decision": "block", "reason": "USAGE_BASELINE_ENDPOINT_MISSING"})
        return 0
    previous = result.get("cost", {}).get("story_usage_baseline")
    if previous:
        result.setdefault("cost", {}).setdefault("story_usage_baseline_revisions", []).append({
            "replaced_at": now(), "reason": reason, "previous": previous,
        })
    capture_usage_baseline(result, receipt)
    result["cost"]["story_usage_baseline_reason"] = reason
    result["cost"].pop("story", None)
    atomic_write_result(path, result)
    dump_json({"decision": "pass", "reason": "USAGE_BASELINE_CAPTURED",
               "baseline": result["cost"]["story_usage_baseline"]})
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
    effective_changed = execution_paths(product, task_id, changed)
    current_subject = subject_for(product, changed)
    current_policy = policy_for(Path(args.harness_root).resolve(), product)
    attestation = verify_attestation(product, commit="HEAD", policy_digest=current_policy)
    committed_result = attestation.get("result") or {}
    committed_current = (
        attestation.get("decision") == "pass"
        and committed_result.get("task_id") == task_id
        and result.get("state") == "validated"
        and result.get("decision") == "pass"
        and not changed
    )
    if not committed_current and invalidate_if_stale(result, current_subject, current_policy):
        atomic_write_result(path, result)
    refresh_assurance(result, product, current_policy, phase="status-head")
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
    effective_changed = execution_paths(product, task_id, changed)
    tier_floor = result["tier"]["effective"]
    subject = subject_for(product, changed)
    current_policy = policy_for(harness, product)
    invalidate_if_stale(result, subject, current_policy)
    result["subject"] = {"kind": "worktree", "digest": subject, "paths": changed}
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
        "tier", subject, current_policy, effective_changed, tier_floor, tools,
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
        effective = classify_tier(effective_changed, floor=tier_floor,
                                  kind=str(result.get("task", {}).get("kind") or "implementation"))
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
        "scope", subject, current_policy, effective_changed, sorted(declared), tools,
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
        scope_pass = bool(declared) and paths_within_scope(effective_changed, declared)
        scope_check = executed_check(
            decision="pass" if scope_pass else "block",
            fingerprint=scope_fingerprint, subject_digest=subject,
            policy_digest=current_policy, completed_at=now(),
        )
    result["checks"]["scope"] = scope_check
    result["invariants"]["scope"] = scope_check["decision"]
    mechanical = mechanical_code_health(product, effective_changed, tier=effective)
    mechanical["fingerprint"] = fingerprint(
        "code_health", subject, result["policy_digest"], effective_changed, effective)
    gc_result = invoke_gc_once(result, task_root, mechanical, changed, product) if mechanical["agent_required"] else None
    if gc_result:
        changed_after = changed_since_baseline(product, baseline) if baseline.is_file() else git_changed(product)
        effective_after = execution_paths(product, task_id, changed_after)
        subject_after = subject_for(product, changed_after)
        if subject_after != subject:
            changed, subject = changed_after, subject_after
            effective_changed = effective_after
            effective = classify_tier(effective_changed, floor=effective,
                                      kind=str(result.get("task", {}).get("kind") or "implementation"))
            result["tier"]["effective"] = effective
            result["subject"] = {"kind": "worktree", "digest": subject, "paths": changed}
            tier_fingerprint = fingerprint(
                "tier", subject, current_policy, effective_changed, tier_floor, tools,
            )
            result["checks"]["tier"] = executed_check(
                decision="pass", fingerprint=tier_fingerprint,
                subject_digest=subject, policy_digest=current_policy,
                completed_at=now(), effective_tier=effective,
            )
            scope_pass = bool(declared) and paths_within_scope(effective_changed, declared)
            scope_fingerprint = fingerprint(
                "scope", subject, current_policy, effective_changed, sorted(declared), tools,
            )
            result["checks"]["scope"] = executed_check(
                decision="pass" if scope_pass else "block",
                fingerprint=scope_fingerprint, subject_digest=subject,
                policy_digest=current_policy, completed_at=now(),
            )
            result["invariants"]["scope"] = result["checks"]["scope"]["decision"]
            mechanical = mechanical_code_health(product, effective_changed, tier=effective)
            mechanical["fingerprint"] = fingerprint(
                "code_health", subject, result["policy_digest"], effective_changed, effective
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
        result["checks"] = checks_for_tier(result["checks"], effective)
    result["invariants"]["risk_validation"] = "pass" if all(
        check.get("decision") == "pass" and not check.get("stale")
        for check in result["checks"].values()
    ) else "block"
    result["invariants"]["final_result"] = "pass"
    result["cost"]["harness"]["gate_duration_ms"] += int((time.monotonic() - started) * 1000)
    apply_automatic_usage(result, task_id=task_id, subject_digest=subject,
                          policy_digest=result["policy_digest"])
    receipt = automatic_receipt(task_id, subject, result["policy_digest"])
    apply_story_usage(result, receipt)
    enforce_budget(result)
    finalize(result, finish_decision)
    refresh_assurance(result, product, result["policy_digest"], phase="pre-commit-head")
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
