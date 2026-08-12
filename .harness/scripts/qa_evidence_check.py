#!/usr/bin/env python3
"""Validate QA sign-off plus TEST/REVIEW reports for active product tasks."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from harness_output import dump_json
from task_contract_check import parse_task_rows
from workspace_paths import Phase0Layout, load_active_planning_gate, load_layout


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
    paths: list[Path] = []
    if wid:
        paths.append(layout.agent_workspace / "tasks" / wid / f"qa_approved_{task_id}.json")
    paths.append(layout.agent_workspace / f"qa_approved_{task_id}.json")
    return paths


def first_existing(paths: list[Path]) -> Path | None:
    return next((p for p in paths if p.is_file()), None)


def validate_qa_json(path: Path, task_id: str) -> list[str]:
    data = load_json(path)
    issues: list[str] = []
    if not data:
        return [f"QA_JSON_INVALID:{path}"]
    if data.get("decision") != "pass":
        issues.append(f"QA_NOT_PASS:{path}")
    if data.get("task_id") != task_id:
        issues.append(f"QA_TASK_MISMATCH:{path}")
    if data.get("reviewer") != "qa-evaluator":
        issues.append(f"QA_REVIEWER_INVALID:{path}")
    if data.get("structure_gate") != "pass":
        issues.append(f"QA_STRUCTURE_MISSING:{path}")
    if "paths_reviewed" not in data:
        issues.append(f"QA_PATHS_REVIEWED_MISSING:{path}")
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
    if kind == "TEST":
        return bool(re.search(r"-\s+\[[xX]\]\s+通过|结论[:：]\s*通过|TEST[_ ]?PASS|测试通过", text))
    return bool(re.search(r"-\s+\[[xX]\]\s+可进入|结论[:：]\s*可进入|REVIEW[_ ]?PASS|审查通过", text))


def validate_report(path: Path | None, kind: str, candidates: list[Path]) -> list[str]:
    if not path:
        preview = ", ".join(str(p) for p in candidates)
        return [f"{kind}_REPORT_MISSING: expected one of {preview}"]
    if not report_passes(path, kind):
        return [f"{kind}_REPORT_NOT_PASSED:{path}"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--task-dir", default="")
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
    if table_issues or not rows:
        emit("block", "QA_EVIDENCE_NO_TASK_CONTRACT: " + "; ".join(table_issues), task_count=len(rows))
        return 0

    wid = work_item_id(planning_gate)
    issues: list[str] = []
    checked: list[dict[str, str]] = []
    for row in rows:
        task_id = row.get("id", "").strip("` ")
        qa_path = first_existing(qa_candidates(layout, wid, task_id))
        if not qa_path:
            issues.append(f"QA_SIGNOFF_MISSING:{task_id}")
            continue
        issues.extend(validate_qa_json(qa_path, task_id))

        test_candidates = report_candidates(layout.test_reports_dir, wid, task_dir, task_id, "TEST")
        review_candidates = report_candidates(layout.review_reports_dir, wid, task_dir, task_id, "REVIEW")
        test_path = first_existing(test_candidates)
        review_path = first_existing(review_candidates)
        issues.extend(validate_report(test_path, "TEST", test_candidates))
        issues.extend(validate_report(review_path, "REVIEW", review_candidates))
        checked.append(
            {
                "task_id": task_id,
                "qa": layout.rel(qa_path),
                "test": layout.rel(test_path) if test_path else "",
                "review": layout.rel(review_path) if review_path else "",
            }
        )

    if issues:
        emit("block", "QA_EVIDENCE_INVALID: " + "; ".join(issues), checked=checked, task_count=len(rows))
        return 0

    emit("pass", f"QA_EVIDENCE_OK: {len(rows)} 个任务具备 QA + TEST + REVIEW 证据", checked=checked, task_count=len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
