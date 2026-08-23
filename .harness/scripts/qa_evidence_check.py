#!/usr/bin/env python3
"""Validate QA sign-off plus TEST/REVIEW reports for active product tasks."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from harness_execution_authority import (
    FIELDS as AUTHORITY_FIELDS,
    SCHEMA as AUTHORITY_SCHEMA,
    trust_from_installation,
    validate_receipt as validate_authority_receipt,
)
from harness_output import dump_json
from harness_task_resolution import valid_task_id
from task_contract_check import parse_task_rows
from workspace_paths import Phase0Layout, load_active_planning_gate, load_layout
from harness_runtime import canonical_digest, load_result, policy_for, subject_for
from worktree_baseline import changed_since_baseline


def emit(decision: str, reason: str, **extra: Any) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def work_item_id(gate: dict[str, Any]) -> str:
    return str((gate.get("work_item") or {}).get("id") or "").strip()


def task_dir_from_args(args: argparse.Namespace, layout: Phase0Layout, gate: dict[str, Any]) -> Path | None:
    raw = args.task_dir or str(gate.get("task_dir") or "")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (layout.product_root / raw).resolve()
    return path


def qa_candidates(layout: Phase0Layout, wid: str, task_id: str) -> list[Path]:
    if not valid_task_id(task_id) or (wid and not valid_task_id(wid)):
        return []
    if wid:
        return [layout.agent_workspace / "tasks" / wid / f"qa_approved_{task_id}.json"]
    return [layout.agent_workspace / f"qa_approved_{task_id}.json"]


def first_existing(paths: list[Path]) -> Path | None:
    return next((p for p in paths if p.is_file()), None)


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


def _bound_path(layout: Phase0Layout, ref: str) -> Path | None:
    if not ref or Path(ref).is_absolute():
        return None
    try:
        path = (layout.product_root / ref).resolve()
        path.relative_to(layout.product_root.resolve())
    except (OSError, ValueError):
        return None
    return path


REVIEWER_IDENTITY_SCHEMA = AUTHORITY_SCHEMA
REVIEWER_IDENTITY_FIELDS = AUTHORITY_FIELDS


def validate_reviewer_identity(
    receipt: Any, *, work_item_id: str, task_id: str,
    harness_root: Path | None = None,
) -> tuple[str, str]:
    if not isinstance(receipt, dict) or set(receipt) != REVIEWER_IDENTITY_FIELDS:
        return "", "QA_REVIEWER_IDENTITY_RECEIPT_INVALID"
    claims = receipt.get("claims")
    if not isinstance(claims, dict) or set(claims) != {
        "issued_by", "reviewer_role", "session_id", "work_item_id", "task_id",
    }:
        return "", "QA_REVIEWER_IDENTITY_RECEIPT_INVALID"
    session_id = str(claims.get("session_id") or "").strip()
    if (
        claims.get("issued_by") != "codex-host"
        or claims.get("reviewer_role") != "qa-evaluator"
        or session_id in {"", "unknown"}
        or claims.get("work_item_id") != work_item_id
        or claims.get("task_id") != task_id
    ):
        return "", "QA_REVIEWER_IDENTITY_RECEIPT_INVALID"
    try:
        trust = trust_from_installation(
            harness_root or Path(__file__).resolve().parents[2],
            "qa-reviewer-identity", "harness-qa-reviewer",
        )
    except ValueError:
        return "", "QA_REVIEWER_IDENTITY_TRUST_REQUIRED"
    binding = {"work_item_id": work_item_id, "task_id": task_id}
    authority = validate_authority_receipt(
        receipt,
        trust=trust,
        authority="qa-reviewer-identity",
        action="qa-signoff",
        subject_digest=canonical_digest(binding),
        provider="codex-host",
        input_digest=canonical_digest({**binding, "reviewer_role": "qa-evaluator"}),
        output_digest=canonical_digest({"session_id": session_id}),
        claims=claims,
    )
    return (
        (session_id, "")
        if authority["decision"] == "pass"
        else ("", "QA_REVIEWER_IDENTITY_RECEIPT_INVALID")
    )


def implementer_session_from_result(data: Any) -> str:
    if not isinstance(data, dict):
        return "unknown"
    cost = data.get("cost")
    if not isinstance(cost, dict):
        return "unknown"
    baseline = cost.get("story_usage_baseline")
    if not isinstance(baseline, dict):
        return "unknown"
    session_id = baseline.get("session_id")
    if not isinstance(session_id, str):
        return "unknown"
    return session_id.strip() or "unknown"


def _current_task_identity(layout: Phase0Layout, wid: str) -> tuple[bool, str, list[str]]:
    path = layout.runs_root / "tasks" / wid / "result.json"
    if not path.is_file():
        return False, "unknown", []
    try:
        data = load_result(path)
    except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return True, "unknown", [f"QA_RESULT_INVALID:{path}"]
    issues = []
    if data.get("task_id") != wid:
        issues.append(f"QA_RESULT_TASK_MISMATCH:{path}")
    tier = data.get("tier") or {}
    current_tier = str(tier.get("effective") or tier.get("initial") or "")
    governed = current_tier in {"standard", "strict"}
    return governed, implementer_session_from_result(data), issues


def _validate_v2_binding(
    data: dict[str, Any], path: Path, layout: Phase0Layout, wid: str,
) -> list[str]:
    governed, current_implementer, issues = _current_task_identity(layout, wid)
    if data.get("schema") != "harness-qa-receipt-v2":
        if governed:
            issues.append(f"QA_V2_RECEIPT_REQUIRED:{path}")
        return issues
    reviewer_session, identity_issue = validate_reviewer_identity(
        data.get("reviewer_identity_receipt"), work_item_id=wid,
        task_id=str(data.get("task_id") or ""),
        harness_root=layout.harness_root,
    )
    if identity_issue:
        issues.append(f"{identity_issue}:{path}")
    if current_implementer == "unknown":
        issues.append(f"QA_IMPLEMENTER_IDENTITY_UNPROVEN:{path}")
    if data.get("implementer_session_id") != current_implementer:
        issues.append(f"QA_IMPLEMENTER_SESSION_MISMATCH:{path}")
    if data.get("reviewer_session_id") != reviewer_session:
        issues.append(f"QA_REVIEWER_SESSION_MISMATCH:{path}")
    if (data.get("independent") is not True or not reviewer_session
            or reviewer_session == current_implementer):
        issues.append(f"QA_INDEPENDENCE_UNPROVEN:{path}")
    baseline = layout.runs_root / "tasks" / wid / "worktree_baseline.json"
    try:
        changed = changed_since_baseline(layout.product_root, baseline)
    except ValueError:
        changed = []
        issues.append(f"QA_BASELINE_INVALID:{path}")
    expected_subject = subject_for(layout.product_root, changed)
    expected_policy = policy_for(layout.harness_root, layout.product_root)
    if data.get("paths_reviewed") != changed:
        issues.append(f"QA_PATHS_STALE:{path}")
    if data.get("paths_digest") != canonical_digest(changed):
        issues.append(f"QA_PATHS_DIGEST_MISMATCH:{path}")
    if data.get("subject_digest") != expected_subject:
        issues.append(f"QA_SUBJECT_MISMATCH:{path}")
    if data.get("policy_digest") != expected_policy:
        issues.append(f"QA_POLICY_MISMATCH:{path}")
    bundle = _bound_path(layout, str(data.get("bundle_ref") or ""))
    try:
        bundle_data = json.loads(bundle.read_text(encoding="utf-8")) if bundle else {}
    except (OSError, json.JSONDecodeError):
        bundle_data = {}
    embedded_digest = str(bundle_data.pop("bundle_digest", "")) if isinstance(bundle_data, dict) else ""
    expected_bundle_digest = canonical_digest(bundle_data) if bundle_data else ""
    if (not bundle_data or embedded_digest != expected_bundle_digest
            or data.get("bundle_digest") != expected_bundle_digest
            or bundle_data.get("subject_digest") != expected_subject
            or bundle_data.get("policy_digest") != expected_policy):
        issues.append(f"QA_BUNDLE_INVALID:{path}")
    for kind in ("test", "review"):
        report = _bound_path(layout, str(data.get(f"{kind}_ref") or ""))
        if report is None or data.get(f"{kind}_digest") != _sha(report):
            issues.append(f"QA_{kind.upper()}_DIGEST_MISMATCH:{path}")
    return issues


def validate_qa_json(
    path: Path, task_id: str, wid: str = "", layout: Phase0Layout | None = None,
) -> list[str]:
    data = load_json(path)
    issues: list[str] = []
    if not data:
        return [f"QA_JSON_INVALID:{path}"]
    if data.get("decision") != "pass":
        issues.append(f"QA_NOT_PASS:{path}")
    if data.get("task_id") != task_id:
        issues.append(f"QA_TASK_MISMATCH:{path}")
    if wid and data.get("work_item_id") != wid:
        issues.append(f"QA_WORK_ITEM_MISMATCH:{path}")
    if data.get("reviewer") != "qa-evaluator":
        issues.append(f"QA_REVIEWER_INVALID:{path}")
    if data.get("structure_gate") != "pass":
        issues.append(f"QA_STRUCTURE_MISSING:{path}")
    if "paths_reviewed" not in data:
        issues.append(f"QA_PATHS_REVIEWED_MISSING:{path}")
    if layout is not None and wid:
        issues.extend(_validate_v2_binding(data, path, layout, wid))
    return issues


def report_candidates(root: Path, wid: str, task_dir: Path, task_id: str, suffix: str) -> list[Path]:
    names = []
    if wid:
        names.extend([f"{wid}-{task_id}-{suffix}.md", f"{wid}-{suffix}.md"])
    names.extend(
        [
            f"{task_id}-{suffix}.md",
            f"{task_dir.name}-{task_id}-{suffix}.md",
            f"{task_dir.name}-{suffix}.md",
        ]
    )
    return [root / name for name in names]


def report_passes(path: Path, kind: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    canonical_pass = bool(
        re.search(r"^\s*(?:-\s*)?结论[:：]\s*`?pass`?\s*$", text, re.MULTILINE | re.IGNORECASE)
    )
    if kind == "TEST":
        return canonical_pass or bool(
            re.search(r"-\s+\[[xX]\]\s+通过|结论[:：]\s*通过|TEST[_ ]?PASS|测试通过", text)
        )
    return canonical_pass or bool(
        re.search(r"-\s+\[[xX]\]\s+可进入|结论[:：]\s*可进入|REVIEW[_ ]?PASS|审查通过", text)
    )


def validate_report(path: Path | None, kind: str, candidates: list[Path]) -> list[str]:
    if not path:
        preview = ", ".join(str(p) for p in candidates)
        return [f"{kind}_REPORT_MISSING: expected one of {preview}"]
    if not report_passes(path, kind):
        return [f"{kind}_REPORT_NOT_PASSED:{path}"]
    return []


def validate_task_entry(
    layout: Phase0Layout, task_dir: Path, wid: str, row: dict[str, str],
) -> tuple[list[str], dict[str, str] | None]:
    """Validate one QA unit without rereading or mutating shared state."""
    task_id = row.get("id", "").strip("` ")
    if not valid_task_id(task_id):
        return [f"QA_TASK_ID_INVALID:{task_id}"], None
    issues: list[str] = []
    qa_path = first_existing(qa_candidates(layout, wid, task_id))
    if not qa_path:
        return [f"QA_SIGNOFF_MISSING:{task_id}"], None
    issues.extend(validate_qa_json(qa_path, task_id, wid, layout))
    test_candidates = report_candidates(layout.test_reports_dir, wid, task_dir, task_id, "TEST")
    review_candidates = report_candidates(layout.review_reports_dir, wid, task_dir, task_id, "REVIEW")
    test_path = first_existing(test_candidates)
    review_path = first_existing(review_candidates)
    issues.extend(validate_report(test_path, "TEST", test_candidates))
    issues.extend(validate_report(review_path, "REVIEW", review_candidates))
    return issues, {
        "task_id": task_id,
        "qa": layout.rel(qa_path),
        "test": layout.rel(test_path) if test_path else "",
        "review": layout.rel(review_path) if review_path else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--task-dir", default="")
    parser.add_argument("--only-task-id", default="")
    args = parser.parse_args()

    layout = load_layout(
        Path(args.harness_root).resolve(),
        Path(args.product_root).resolve() if args.product_root else None,
    )
    planning_gate = load_active_planning_gate(layout)
    if not planning_gate:
        emit("block", "QA_EVIDENCE_NO_PLANNING_GATE: run planning_gate.sh first")
        return 0

    task_dir = task_dir_from_args(args, layout, planning_gate)
    if task_dir is None:
        emit("pass", "QA_EVIDENCE_SKIPPED: no task_dir for L1/local task", task_count=0)
        return 0
    if not task_dir.is_dir():
        emit("block", f"QA_EVIDENCE_TASK_DIR_MISSING: {task_dir}")
        return 0

    plan = task_dir / "03-实施方案.md"
    if not plan.is_file():
        emit("block", f"QA_EVIDENCE_PLAN_MISSING: {plan}")
        return 0

    rows, table_issues = parse_task_rows(plan.read_text(encoding="utf-8", errors="ignore"))
    if args.only_task_id:
        rows = [row for row in rows if row.get("id", "").strip("` ") == args.only_task_id]
        if not rows:
            emit("block", f"QA_TASK_ID_NOT_IN_PLAN:{args.only_task_id}", task_count=0)
            return 0
    if table_issues or not rows:
        emit("block", "QA_EVIDENCE_NO_TASK_CONTRACT: " + "; ".join(table_issues), task_count=len(rows))
        return 0

    wid = work_item_id(planning_gate)
    issues: list[str] = []
    if wid and not valid_task_id(wid):
        emit("block", "QA_EVIDENCE_WORK_ITEM_ID_INVALID", task_count=len(rows))
        return 0
    checked: list[dict[str, str]] = []
    for row in rows:
        row_issues, row_checked = validate_task_entry(layout, task_dir, wid, row)
        issues.extend(row_issues)
        if row_checked:
            checked.append(row_checked)

    if issues:
        emit("block", "QA_EVIDENCE_INVALID: " + "; ".join(issues), checked=checked, task_count=len(rows))
        return 0

    emit("pass", f"QA_EVIDENCE_OK: {len(rows)} 个任务具备 QA + TEST + REVIEW 证据", checked=checked, task_count=len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
