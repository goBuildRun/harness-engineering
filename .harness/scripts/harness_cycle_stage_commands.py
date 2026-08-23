#!/usr/bin/env python3
"""Stage CLI command and frozen candidate integration."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from harness_candidate import (
    bound_candidate, build_snapshot, candidate_paths, current_candidate, refresh_evidence,
    release_evidence_paths as stable_release_evidence_paths, write_snapshot,
)
from harness_cycle_stages import begin_stage, finish_stage, stage_start_readiness
from harness_output import dump_json
from harness_phase_handoff import write as write_phase_handoff
from harness_runtime import (
    atomic_write_result, canonical_digest, load_result, result_path, task_operation_lock,
)
from harness_gate_work_items import committed_task_binding, validate_planning_credential
from harness_task_resolution import valid_task_id
from harness_timing import budget_status, ledger_path, read_events, summarize_events
from workspace_paths import load_active_planning_gate, load_layout
from worktree_baseline import changed_since_baseline


def _candidate_snapshot_path(path: Path) -> Path:
    return path.parent / "candidate-snapshot.json"


def _freeze_candidate(result: dict[str, Any], product: Path, path: Path) -> dict[str, Any]:
    baseline = path.parent / "worktree_baseline.json"
    changed = changed_since_baseline(product, baseline)
    try:
        baseline_payload = json.loads(baseline.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("CANDIDATE_BASELINE_INVALID") from exc
    baseline_commit = str(baseline_payload.get("repo_head") or "").strip()
    if not baseline_commit:
        raise ValueError("CANDIDATE_BASELINE_INVALID")
    snapshot = build_snapshot(product, changed, baseline_commit=baseline_commit)
    current = current_candidate(
        product, snapshot, changed_since_baseline(product, baseline),
    )
    if current["decision"] == "block":
        raise ValueError(str(current["reason"]))
    write_snapshot(_candidate_snapshot_path(path), snapshot)
    result["candidate"] = {
        "schema": snapshot["schema"],
        "digest": snapshot["candidate_digest"],
        "paths": snapshot["candidate_paths"],
        "baseline_commit": snapshot["baseline_commit"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "snapshot": "candidate-snapshot.json",
        "frozen_at": snapshot["captured_at"],
    }
    return snapshot


def load_candidate_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(_candidate_snapshot_path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def release_evidence_paths(product: Path, path: Path) -> list[str]:
    """Return release evidence without the mutable result control record."""
    baseline = path.parent / "worktree_baseline.json"
    changed = changed_since_baseline(product, baseline)
    result_ref = path.resolve().relative_to(product.resolve()).as_posix()
    return stable_release_evidence_paths(changed, result_ref)


def validate_qa_candidate_binding(
    product: Path, result: dict[str, Any], payload: dict[str, Any], *,
    snapshot: dict[str, Any] | None = None,
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    product = product.resolve()
    candidate = result.get("candidate") or {}
    expected_paths = candidate.get("paths")
    expected_digest = str(candidate.get("digest") or "")
    expected_snapshot = str(candidate.get("snapshot_digest") or "")
    checked = payload.get("checked")
    aggregate_candidate = payload.get("candidate") or {}
    if (
        payload.get("decision") != "pass"
        or not expected_digest
        or not expected_snapshot
        or not isinstance(expected_paths, list)
        or expected_paths != sorted(set(expected_paths))
        or not isinstance(checked, list)
        or not checked
        or payload.get("task_count") != len(checked)
    ):
        return {"decision": "block", "reason": "QA_CANDIDATE_BINDING_INVALID"}
    task_id = str(result.get("task_id") or "")
    task_path = result_path(product, task_id) if valid_task_id(task_id) else None
    snapshot = snapshot or (load_candidate_snapshot(task_path) if task_path else None)
    current = (
        bound_candidate(
            product, snapshot, candidate,
            changed_paths if changed_paths is not None else expected_paths,
        )
        if isinstance(snapshot, dict)
        else {"decision": "block", "reason": "CANDIDATE_NOT_FROZEN"}
    )
    if current["decision"] == "block":
        return {"decision": "block", "reason": "QA_CANDIDATE_STALE"}
    if (
        not isinstance(aggregate_candidate, dict)
        or aggregate_candidate.get("subject_digest") != expected_digest
        or aggregate_candidate.get("snapshot_digest") != expected_snapshot
    ):
        return {"decision": "block", "reason": "QA_CANDIDATE_SNAPSHOT_MISMATCH"}
    for item in checked:
        if not isinstance(item, dict):
            return {"decision": "block", "reason": "QA_CANDIDATE_BINDING_INVALID"}
        ref = str(item.get("qa") or "")
        try:
            receipt_path = (product / ref).resolve()
            receipt_path.relative_to(product)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"decision": "block", "reason": "QA_CANDIDATE_BINDING_INVALID"}
        reviewed = (
            receipt.get("candidate_paths_reviewed")
            if isinstance(receipt, dict) else None
        )
        if not isinstance(reviewed, list) or candidate_paths(reviewed) != expected_paths:
            return {"decision": "block", "reason": "QA_CANDIDATE_PATHS_MISMATCH"}
        if receipt.get("candidate_subject_digest") != expected_digest:
            return {"decision": "block", "reason": "QA_CANDIDATE_SUBJECT_MISMATCH"}
        if receipt.get("candidate_snapshot_digest") != expected_snapshot:
            return {"decision": "block", "reason": "QA_CANDIDATE_SNAPSHOT_MISMATCH"}
    return {
        "decision": "pass", "reason": "QA_CANDIDATE_BOUND",
        "candidate_digest": expected_digest,
        "snapshot_digest": expected_snapshot,
        "qa_aggregate_digest": canonical_digest(payload),
    }


def validate_current_qa_evidence(
    product: Path, task_id: str, result: dict[str, Any],
) -> dict[str, Any]:
    harness = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "HARNESS_PRODUCT_ROOT": str(product),
        "HARNESS_PRETTY": "0",
    }
    try:
        work_item = result.get("work_item") or {}
        work_item_id = str(work_item.get("id") or "")
        provider = str(work_item.get("provider") or "")
        layout = load_layout(harness, product)
        try:
            planning = load_active_planning_gate(layout)
        except (OSError, json.JSONDecodeError):
            planning = {}
        binding = validate_planning_credential(
            harness, product, task_id, planning,
            work_item_id=work_item_id, provider=provider,
        )
        if binding["decision"] == "block":
            return {"decision": "block", "reason": binding["reason"]}
        task_id_path = layout.runs_root / "tasks" / task_id / "active_task.json"
        active_path = task_id_path if os.environ.get("HARNESS_TASK_ID") else layout.runs_root / "active_task.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        if str(active.get("task_id") or "") != task_id or (
            active.get("work_item_id") and str(active["work_item_id"]) != work_item_id
        ):
            return {"decision": "block", "reason": "QA_EVIDENCE_ACTIVE_TASK_MISMATCH"}
        task_binding = committed_task_binding(harness, product, task_id)
        task_dir = str(task_binding.get("task_dir") or "")
        if not task_dir or not Path(task_dir).is_dir():
            return {"decision": "block", "reason": "QA_EVIDENCE_TASK_DIR_REQUIRED"}
        command = [
            "bash", str(harness / ".harness/scripts/qa_evidence_check.sh"),
            "--task-dir", task_dir,
        ]
        completed = subprocess.run(
            command,
            cwd=harness, env=environment, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
        payload = json.loads(completed.stdout)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return {"decision": "block", "reason": "QA_EVIDENCE_CHECK_FAILED"}
    if (
        completed.returncode != 0
        or not isinstance(payload, dict)
        or payload.get("decision") not in {"pass", "block"}
    ):
        return {"decision": "block", "reason": "QA_EVIDENCE_CHECK_FAILED"}
    if payload["decision"] == "pass":
        task_path = result_path(product, task_id)
        snapshot = load_candidate_snapshot(task_path)
        candidate = result.get("candidate") or {}
        payload["candidate"] = {
            "subject_digest": str(candidate.get("digest") or ""),
            "snapshot_digest": str(candidate.get("snapshot_digest") or ""),
        }
        try:
            changed = changed_since_baseline(
                product, task_path.parent / "worktree_baseline.json",
            )
        except (OSError, ValueError):
            return {"decision": "block", "reason": "QA_CANDIDATE_STALE"}
        binding = validate_qa_candidate_binding(
            product, result, payload, snapshot=snapshot, changed_paths=changed,
        )
        if binding["decision"] == "block":
            return binding
        return {**payload, **binding}
    return payload


def validate_current_release_evidence(
    product: Path, task_id: str, result: dict[str, Any],
) -> dict[str, Any]:
    path = result_path(product, task_id)
    try:
        changed = release_evidence_paths(product, path)
    except (OSError, ValueError):
        return {"decision": "block", "reason": "RELEASE_EVIDENCE_BASELINE_INVALID"}
    manifest = result.get("evidence_manifest")
    if not isinstance(manifest, dict) or manifest != refresh_evidence(product, changed):
        return {"decision": "block", "reason": "RELEASE_EVIDENCE_MANIFEST_STALE"}
    if _governed_qa(result):
        qa = validate_current_qa_evidence(product, task_id, result)
        if qa["decision"] == "block":
            return qa
    strict = (result.get("checks") or {}).get("strict_evidence") or {}
    if strict.get("decision") == "pass":
        from harness_strict_gate import validate as validate_strict_evidence

        harness = Path(__file__).resolve().parents[2]
        try:
            planning = load_active_planning_gate(load_layout(harness, product))
        except (OSError, json.JSONDecodeError):
            return {"decision": "block", "reason": "RELEASE_PLANNING_CREDENTIAL_INVALID"}
        work_item = planning.get("work_item") or {}
        production_policy = (
            work_item.get("production_evidence") if isinstance(work_item, dict) else None
        )
        expected_provider = (
            str(work_item.get("provider") or "") if isinstance(work_item, dict) else ""
        )
        strict_status = validate_strict_evidence(
            str((result.get("candidate") or {}).get("digest") or ""),
            production_policy, product, expected_provider,
        )
        if strict_status["decision"] == "block":
            return strict_status
    return {"decision": "pass", "reason": "RELEASE_EVIDENCE_CURRENT"}


def _governed_qa(result: dict[str, Any]) -> bool:
    tier = result.get("tier") or {}
    return str(tier.get("effective") or tier.get("initial") or "unknown") != "lite"


def cmd_stage(args) -> int:
    product = Path(args.product_root).resolve()
    if not valid_task_id(args.task_id):
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    path = result_path(product, args.task_id)
    if not path.is_file():
        dump_json({"decision": "block", "reason": "TASK_NOT_FOUND"})
        return 0
    with task_operation_lock(path):
        result = load_result(path)
        if args.action == "status":
            try:
                outcome = {
                    "decision": budget_status(result["cycle"])["decision"],
                    "reason": "STAGE_STATUS", "cycle": result["cycle"],
                    "ledger": summarize_events(read_events(ledger_path(path))),
                }
            except OSError:
                outcome = {"decision": "block", "reason": "STORY_LEDGER_INVALID"}
        elif args.action == "start":
            snapshot = load_candidate_snapshot(path)
            if args.stage == "independent_qa":
                readiness = stage_start_readiness(
                    result, path, args.task_id, args.stage,
                )
                if readiness["decision"] == "block":
                    outcome = readiness
                else:
                    try:
                        snapshot = _freeze_candidate(result, product, path)
                    except (OSError, ValueError) as exc:
                        outcome = {
                            "decision": "block",
                            "reason": str(exc) or "CANDIDATE_FREEZE_FAILED",
                        }
                    else:
                        outcome = begin_stage(result, path, args.task_id, args.stage)
                        if outcome["decision"] == "pass":
                            outcome["candidate_digest"] = result["candidate"]["digest"]
            elif args.stage in {"deploy_provider", "finalize"}:
                baseline = path.parent / "worktree_baseline.json"
                changed = changed_since_baseline(product, baseline)
                current = (
                    bound_candidate(product, snapshot, result.get("candidate"), changed)
                    if snapshot else {"decision": "block", "reason": "CANDIDATE_NOT_FROZEN"}
                )
                if current["decision"] == "block":
                    outcome = current
                elif args.stage == "deploy_provider" and _governed_qa(result) and (
                    qa := validate_current_qa_evidence(product, args.task_id, result)
                )["decision"] == "block":
                    outcome = qa
                else:
                    outcome = begin_stage(result, path, args.task_id, args.stage)
            else:
                outcome = begin_stage(result, path, args.task_id, args.stage)
            if outcome["decision"] == "pass":
                try:
                    outcome["handoff"] = write_phase_handoff(
                        result, path.parent, str(args.stage),
                    )
                    if args.stage == "deploy_provider":
                        current_paths = release_evidence_paths(product, path)
                        result["evidence_manifest"] = refresh_evidence(
                            product, current_paths,
                        )
                except (OSError, ValueError):
                    outcome = finish_stage(
                        result, path, args.task_id, str(args.stage), decision="block",
                        reason="PHASE_HANDOFF_FAILED", tool_wait_ms="unknown",
                    )
        else:
            wait = int(args.tool_wait_ms) if str(args.tool_wait_ms).isdigit() else "unknown"
            qa: dict[str, Any] = {}
            if (
                args.stage == "independent_qa"
                and args.decision == "pass"
                and _governed_qa(result)
                and (
                    qa := validate_current_qa_evidence(product, args.task_id, result)
                )["decision"] == "block"
            ):
                outcome = qa
            else:
                if args.stage == "independent_qa" and args.decision == "pass":
                    result.setdefault("cycle", {})["qa_candidate_digest"] = str(
                        qa.get("candidate_digest") or ""
                    )
                    result["cycle"]["qa_snapshot_digest"] = str(
                        qa.get("snapshot_digest") or ""
                    )
                    result["cycle"]["qa_aggregate_digest"] = str(
                        qa.get("qa_aggregate_digest") or ""
                    )
                try:
                    outcome = finish_stage(
                        result, path, args.task_id, args.stage, decision=args.decision,
                        reason=args.reason, tool_wait_ms=wait,
                    )
                except OSError:
                    outcome = {"decision": "block", "reason": "STORY_LEDGER_INVALID"}
        if args.action != "status":
            atomic_write_result(path, result)
    dump_json(outcome)
    return 0
