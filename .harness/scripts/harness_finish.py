#!/usr/bin/env python3
"""Finish-command orchestration for the lean Harness runtime."""
from __future__ import annotations

import argparse


def execute_finish(args: argparse.Namespace, *, commands) -> int:
    started = commands.time.monotonic()
    product, harness = commands.Path(args.product_root).resolve(), commands.Path(args.harness_root).resolve()
    task_id, candidates = commands.resolve_task_id(product, args.task_id)
    if not task_id:
        reason = "TASK_ID_INVALID" if candidates == ["TASK_ID_INVALID"] else "TASK_INFERENCE_AMBIGUOUS"
        commands.dump_json({"decision": "block", "reason": reason, "candidates": candidates})
        return 0
    path = commands.result_path(product, task_id)
    if not path.is_file():
        commands.dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    with (
        commands.task_operation_lock(commands.active_task_path(product)),
        commands.task_operation_lock(path),
    ):
        return _execute_finish_locked(
            args, product, harness, task_id, path, started,
            commands=commands,
        )


def _execute_finish_locked(
    args: argparse.Namespace, product, harness, task_id: str,
    path, started: float, *, commands,
) -> int:
    result = commands.load_result(path)
    work_item = result.get("work_item") or {}
    work_item_id = str(work_item.get("id") or "") if isinstance(work_item, dict) else ""
    work_item_provider = (
        str(work_item.get("provider") or "") if isinstance(work_item, dict) else ""
    )
    if not commands.bind_active_task(product, task_id, work_item_id):
        commands.dump_json({"decision": "block", "reason": "ACTIVE_TASK_BINDING_CONFLICT"})
        return 0
    previous_checks = commands.deepcopy(result.get("checks", {}))
    had_checks = bool(result.get("checks"))
    result["blockers"] = []
    if had_checks and result.get("state") != "active":
        result["cost"]["harness"]["reruns"] = int(
            result["cost"]["harness"].get("reruns") or 0
        ) + 1
    task_root, baseline = path.parent, path.parent / "worktree_baseline.json"
    changed = commands.changed_since_baseline(product, baseline) if baseline.is_file() else commands.git_changed(product)
    result_ref = path.resolve().relative_to(product).as_posix()
    effective_changed = commands.execution_paths(product, task_id, changed)
    tier_floor = str(
        (result.get("task") or {}).get("tier_floor")
        or result["tier"]["initial"]
    )
    snapshot = commands.load_candidate_snapshot(path)
    stage_enforced = bool(
        (result.get("cycle") or {}).get("release_readback_enforced")
    )
    if stage_enforced:
        candidate_status = (
            commands.bound_candidate(product, snapshot, result.get("candidate"), changed)
            if snapshot else {"decision": "block", "reason": "CANDIDATE_NOT_FROZEN"}
        )
        if candidate_status["decision"] == "block":
            result["decision"], result["state"] = "block", "blocked"
            result["blockers"] = sorted(
                set(result.get("blockers") or []) | {candidate_status["reason"]}
            )
            result["cycle"]["last_stop"] = candidate_status
            commands.atomic_write_result(path, result)
            commands.dump_json({**candidate_status, "result": result})
            return 0
        subject = str(snapshot["candidate_digest"])
        evidence = commands.refresh_evidence(
            product, commands.release_evidence_paths(changed, result_ref),
        )
        result["evidence_manifest"] = evidence
    else:
        subject = commands.subject_for(product, changed)
    current_policy = commands.policy_for(harness, product)
    attempt = commands.allow_finish_attempt(
        result, subject=subject, policy=current_policy, tier_floor=tier_floor,
    )
    if attempt["decision"] == "block":
        commands.atomic_write_result(path, result)
        commands.dump_json({"decision": "block", "reason": attempt["reason"], "result": result})
        return 0
    finish_span, finish_started_ms = commands.begin_finish_span(
        result, path, task_id, work_item_id, attempt,
    )
    commands.atomic_write_result(path, result)
    finalize_budget = commands.stage_budget_status(result["cycle"], "finalize")
    if finalize_budget["decision"] == "block":
        result["decision"], result["state"] = "block", "blocked"
        result["blockers"] = sorted(
            set(result.get("blockers") or []) | {finalize_budget["reason"]}
        )
        result["cycle"]["last_stop"] = finalize_budget
        commands.complete_finish_span(
            result, path, task_id=task_id, work_item_id=work_item_id,
            span_id=finish_span, started_epoch_ms=finish_started_ms,
            decision="block", reason=finalize_budget["reason"], attempt=attempt,
            tool_wait_ms="unknown",
        )
        commands.atomic_write_result(path, result)
        commands.dump_json({**finalize_budget, "result": result})
        return 0
    commands.invalidate_if_stale(result, subject, current_policy)
    subject_paths = list(snapshot.get("candidate_paths") or []) if stage_enforced else changed
    result["subject"] = {
        "kind": "worktree",
        "digest": subject, "paths": subject_paths,
    }
    result["policy_digest"] = current_policy
    result["blockers"] = [
        blocker for blocker in result.get("blockers", []) if blocker != "INPUT_CHANGED"
    ]
    tools = commands.tool_digest([
        harness / ".harness/scripts/harness_runtime.py",
        harness / ".harness/scripts/harness_scope.py",
        harness / ".harness/scripts/harness_cache.py",
        harness / ".harness/scripts/harness_tier.py",
    ])
    tier_fingerprint = commands.fingerprint(
        "tier", subject, current_policy, effective_changed, tier_floor, tools,
    )
    tier_check = commands.reuse_check(
        previous_checks.get("tier"), gate="tier", fingerprint=tier_fingerprint,
        subject_digest=subject, policy_digest=current_policy,
    )
    if tier_check:
        effective = tier_check["effective_tier"]
        result["cost"]["harness"]["cache_hits"] = int(
            result["cost"]["harness"].get("cache_hits") or 0
        ) + 1
    else:
        effective = commands.classify_tier(effective_changed, floor=tier_floor,
                                  kind=str(result.get("task", {}).get("kind") or "implementation"))
        tier_check = commands.executed_check(
            decision="pass", fingerprint=tier_fingerprint,
            subject_digest=subject, policy_digest=current_policy,
            completed_at=commands.now(), effective_tier=effective,
        )
    result["tier"]["effective"] = effective
    result["checks"]["tier"] = tier_check
    declared = set(result.get("task", {}).get("scope") or [])
    result["invariants"]["task_identity"] = "pass" if result.get("task_id") else "block"
    scope_fingerprint = commands.fingerprint(
        "scope", subject, current_policy, effective_changed, sorted(declared), tools,
    )
    scope_check = commands.reuse_check(
        previous_checks.get("scope"), gate="scope", fingerprint=scope_fingerprint,
        subject_digest=subject, policy_digest=current_policy,
    )
    if scope_check:
        result["cost"]["harness"]["cache_hits"] = int(
            result["cost"]["harness"].get("cache_hits") or 0
        ) + 1
    else:
        scope_pass = bool(declared) and commands.paths_within_scope(effective_changed, declared)
        scope_check = commands.executed_check(
            decision="pass" if scope_pass else "block",
            fingerprint=scope_fingerprint, subject_digest=subject,
            policy_digest=current_policy, completed_at=commands.now(),
        )
    result["checks"]["scope"] = scope_check
    result["invariants"]["scope"] = scope_check["decision"]
    mechanical_changed = (
        commands.execution_paths(product, task_id, subject_paths)
        if stage_enforced else effective_changed
    )
    mechanical = commands.mechanical_code_health(product, mechanical_changed, tier=effective)
    if stage_enforced:
        mechanical["subject_digest"] = subject
    mechanical["fingerprint"] = commands.fingerprint(
        "code_health", subject, result["policy_digest"], effective_changed, effective)
    root_before_gc = commands.budget_status(result["cycle"])
    stage_before_gc = commands.stage_budget_status(result["cycle"], "finalize")
    gc_timeout_ms = max(0, min(
        int(root_before_gc.get("remaining_ms") or 0),
        int(stage_before_gc.get("remaining_ms") or 0),
    ))
    gc_result = (
        commands.invoke_gc_once(
            result, task_root, mechanical, mechanical_changed, product,
            timeout_ms=gc_timeout_ms,
        )
        if mechanical["agent_required"] and gc_timeout_ms > 0 else None
    )
    if mechanical["agent_required"] and gc_timeout_ms <= 0:
        result["blockers"] = sorted(
            set(result.get("blockers") or []) | {"STORY_BUDGET_EXCEEDED"}
        )
    if gc_result:
        changed_after = commands.changed_since_baseline(product, baseline) if baseline.is_file() else commands.git_changed(product)
        effective_after = commands.execution_paths(product, task_id, changed_after)
        candidate_after = (
            commands.current_candidate(product, snapshot, changed_after)
            if stage_enforced else None
        )
        if candidate_after and candidate_after["decision"] == "block":
            result["blockers"] = sorted(
                set(result.get("blockers") or []) | {candidate_after["reason"]}
            )
        elif stage_enforced:
            changed = changed_after
            effective_changed = effective_after
            result["evidence_manifest"] = commands.refresh_evidence(
                product, commands.release_evidence_paths(changed_after, result_ref),
            )
        else:
            subject_after = commands.subject_for(product, changed_after)
            if subject_after == subject:
                subject_after = ""
        if not stage_enforced and subject_after:
            changed, subject = changed_after, subject_after
            effective_changed = effective_after
            effective = commands.classify_tier(effective_changed, floor=effective,
                                      kind=str(result.get("task", {}).get("kind") or "implementation"))
            result["tier"]["effective"] = effective
            result["subject"] = {"kind": "worktree", "digest": subject, "paths": changed}
            tier_fingerprint = commands.fingerprint(
                "tier", subject, current_policy, effective_changed, tier_floor, tools,
            )
            result["checks"]["tier"] = commands.executed_check(
                decision="pass", fingerprint=tier_fingerprint,
                subject_digest=subject, policy_digest=current_policy,
                completed_at=commands.now(), effective_tier=effective,
            )
            scope_pass = bool(declared) and commands.paths_within_scope(effective_changed, declared)
            scope_fingerprint = commands.fingerprint(
                "scope", subject, current_policy, effective_changed, sorted(declared), tools,
            )
            result["checks"]["scope"] = commands.executed_check(
                decision="pass" if scope_pass else "block",
                fingerprint=scope_fingerprint, subject_digest=subject,
                policy_digest=current_policy, completed_at=commands.now(),
            )
            result["invariants"]["scope"] = result["checks"]["scope"]["decision"]
            mechanical = commands.mechanical_code_health(product, effective_changed, tier=effective)
            mechanical["fingerprint"] = commands.fingerprint(
                "code_health", subject, result["policy_digest"], effective_changed, effective
            )
            result["checks"].pop("tests", None)
            result["checks"].pop("structure", None)
    commands.apply_code_health(result, mechanical, gc_result=gc_result, subject_digest=subject,
                      policy_digest=result["policy_digest"])
    post_gate_inputs_changed = False
    if not args.skip_legacy_gates:
        root_remaining = commands.budget_status(result["cycle"])
        finalize_remaining = commands.stage_budget_status(result["cycle"], "finalize")
        controlled_remaining_ms = max(0, min(
            int(root_remaining.get("remaining_ms") or 0),
            int(finalize_remaining.get("remaining_ms") or 0),
        ))
        gates = commands.run_gate_plan(
            harness, product, tier=effective, subject_digest=subject,
            policy_digest=result["policy_digest"], changed_files=changed,
            previous_checks=previous_checks,
            remaining_budget_ms=controlled_remaining_ms,
            ledger=commands.ledger_path(path), task_id=task_id, work_item_id=work_item_id,
            work_item_provider=work_item_provider,
        )
        result["cost"]["harness"]["cache_hits"] = int(
            result["cost"]["harness"].get("cache_hits") or 0
        ) + int(gates.get("cache_hits") or 0)
        result["checks"].update(gates["checks"])
        if gates["missing"]:
            result["blockers"].append("REQUIRED_GATE_MISSING")
        result["checks"] = commands.checks_for_tier(result["checks"], effective)
        changed_after_gates = (
            commands.changed_since_baseline(product, baseline) if baseline.is_file()
            else commands.git_changed(product)
        )
        candidate_after_gates = (
            commands.current_candidate(product, snapshot, changed_after_gates)
            if stage_enforced else None
        )
        subject_after_gates = (
            str(snapshot["candidate_digest"])
            if stage_enforced else commands.subject_for(product, changed_after_gates)
        )
        policy_after_gates = commands.policy_for(harness, product)
        post_gate_inputs_changed = (
            (candidate_after_gates is not None and candidate_after_gates["decision"] == "block")
            or subject_after_gates != subject
            or policy_after_gates != result["policy_digest"]
        )
        if post_gate_inputs_changed:
            commands.invalidate_if_stale(result, subject_after_gates, policy_after_gates)
            changed, subject = changed_after_gates, subject_after_gates
            result["subject"] = {
                "kind": "worktree", "digest": subject,
                "paths": subject_paths if stage_enforced else changed,
            }
            result["policy_digest"] = policy_after_gates
            result["invariants"]["scope"] = "pending"
        elif stage_enforced:
            changed = changed_after_gates
            result["evidence_manifest"] = commands.refresh_evidence(
                product,
                commands.release_evidence_paths(changed_after_gates, result_ref),
            )
    result["invariants"]["risk_validation"] = (
        "pending" if post_gate_inputs_changed else
        "pass" if all(
            check.get("decision") == "pass" and not check.get("stale")
            for check in result["checks"].values()
        ) else "block"
    )
    result["invariants"]["final_result"] = "pending" if post_gate_inputs_changed else "pass"
    result["cost"]["harness"]["gate_duration_ms"] += int((commands.time.monotonic() - started) * 1000)
    commands.apply_automatic_usage(result, task_id=task_id, subject_digest=subject,
                          policy_digest=result["policy_digest"])
    receipt = commands.automatic_receipt(task_id, subject, result["policy_digest"])
    commands.apply_story_usage(result, receipt)
    epic_id = str((result.get("task") or {}).get("epic_id") or "")
    if epic_id:
        results = []
        for candidate in (commands.workspace_root(product) / "runs" / "tasks").glob("*/result.json"):
            try:
                results.append(commands.load_result(candidate))
            except (OSError, ValueError):
                continue
        results = [item for item in results if item.get("task_id") != task_id] + [result]
        result["cost"]["epic"] = commands.aggregate_epic_usage(epic_id, results)
    commands.enforce_budget(result)
    final_root_budget = commands.budget_status(result["cycle"])
    final_stage_budget = commands.stage_budget_status(result["cycle"], "finalize")
    for control in (final_root_budget, final_stage_budget):
        if control["decision"] == "block":
            result["blockers"] = sorted(
                set(result.get("blockers") or []) | {control["reason"]}
            )
            result["cycle"]["last_stop"] = control
    commands.finalize(result, commands.finish_decision)
    commands.refresh_assurance(result, product, result["policy_digest"], phase="pre-commit-head")
    closure_root_budget = commands.budget_status(result["cycle"])
    closure_stage_budget = commands.stage_budget_status(result["cycle"], "finalize")
    for control in (closure_root_budget, closure_stage_budget):
        if control["decision"] == "block":
            result["decision"], result["state"] = "block", "blocked"
            result["blockers"] = sorted(
                set(result.get("blockers") or []) | {control["reason"]}
            )
            result["cycle"]["last_stop"] = control
    reason = (
        "INPUT_CHANGED" if post_gate_inputs_changed
        else result["blockers"][0] if result["blockers"] else "FINISH_OK"
    )
    try:
        commands.complete_finish_span(
            result, path, task_id=task_id, work_item_id=work_item_id, span_id=finish_span,
            started_epoch_ms=finish_started_ms, decision=result["decision"], reason=reason,
            attempt=attempt, tool_wait_ms="unknown",
        )
    except OSError:
        result["decision"], result["state"] = "block", "blocked"
        result["blockers"] = sorted(set(result["blockers"]) | {"STORY_LEDGER_WRITE_FAILED"})
        reason = "STORY_LEDGER_WRITE_FAILED"
    commands.atomic_write_result(path, result)
    commands.dump_json({"decision": result["decision"], "reason": reason, "result": result})
    return 0
