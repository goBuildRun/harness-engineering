#!/usr/bin/env python3
"""Bounded parallel QA unit validation with deterministic aggregation."""
from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from pathlib import Path

from ael_output import dump_json
from ael_task_resolution import valid_task_id
from qa_evidence_check import task_dir_from_args, validate_task_entry, work_item_id
from task_contract_check import parse_task_rows, validate
from business_paths import load_business_roots
from workspace_paths import load_active_planning_gate, load_layout


def fanout(*, ael_root: Path, product_root: Path, task_dir_arg: str = "",
           timeout_seconds: float = 120.0) -> dict:
    layout = load_layout(ael_root.resolve(), product_root.resolve())
    gate = load_active_planning_gate(layout)
    if not gate:
        return {"decision": "block", "reason": "QA_EVIDENCE_NO_PLANNING_GATE"}
    task_dir = task_dir_from_args(
        argparse.Namespace(task_dir=task_dir_arg), layout, gate,
    )
    if task_dir is None:
        return {"decision": "pass", "reason": "QA_EVIDENCE_SKIPPED", "task_count": 0,
                "fanout": {"parallel": False, "units": []}}
    plan = task_dir / "03-实施方案.md"
    if not plan.is_file():
        return {"decision": "block", "reason": "QA_EVIDENCE_PLAN_MISSING"}
    plan_text = plan.read_text(encoding="utf-8", errors="ignore")
    rows, table_issues = parse_task_rows(plan_text)
    contract_issues = table_issues + validate(
        rows, plan_text, load_business_roots(layout.ael_root, product_root=layout.product_root),
    )
    if contract_issues or not rows:
        return {"decision": "block", "reason": "QA_EVIDENCE_NO_TASK_CONTRACT",
                "issues": sorted(contract_issues)}
    wid = work_item_id(gate)
    if wid and not valid_task_id(wid):
        return {"decision": "block", "reason": "QA_EVIDENCE_WORK_ITEM_ID_INVALID"}
    started = time.monotonic()
    issues: list[str] = []
    checked: list[dict] = []
    workers = min(5, len(rows))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            pool.submit(validate_task_entry, layout, task_dir, wid, row): str(row.get("id") or "")
            for row in rows
        }
        outcomes = []
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        try:
            iterator = as_completed(futures, timeout=max(0.1, timeout_seconds))
            for future in iterator:
                task_id = futures[future]
                try:
                    outcomes.append(future.result(timeout=max(0.0, deadline - time.monotonic())))
                except Exception as exc:
                    outcomes.append(([f"QA_UNIT_EXCEPTION:{task_id}:{type(exc).__name__}"], None))
        except TimeoutError:
            for future, task_id in futures.items():
                if not future.done():
                    outcomes.append(([f"QA_UNIT_TIMEOUT:{task_id}"], None))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for row_issues, row_checked in sorted(
        outcomes, key=lambda item: str((item[1] or {}).get("task_id") or ""),
    ):
        issues.extend(row_issues)
        if row_checked:
            checked.append(row_checked)
    return {
        "decision": "pass" if not issues else "block",
        "reason": "QA_EVIDENCE_FANOUT_OK" if not issues else "QA_EVIDENCE_FANOUT_BLOCK",
        "checked": checked,
        "task_count": len(rows),
        "fanout": {
            "parallel": workers > 1,
            "max_workers": workers,
            "units": [str(row.get("id") or "") for row in rows],
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
        **({"issues": sorted(issues)} if issues else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ael-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--product-root", default="")
    parser.add_argument("--task-dir", default="")
    # Retain the legacy checker identity in the command contract for older
    # integrations; fan-out never invokes this serial checker.
    parser.add_argument("--compatibility-source", default="")
    parser.add_argument(
        "--timeout-seconds", type=float,
        default=float(os.environ.get("AEL_QA_TIMEOUT_SECONDS", "120")),
    )
    args = parser.parse_args()
    try:
        result = fanout(
            ael_root=Path(args.ael_root), product_root=Path(args.product_root).resolve()
            if args.product_root else Path.cwd(), task_dir_arg=args.task_dir,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        result = {"decision": "block", "reason": f"QA_EVIDENCE_FANOUT_INVALID: {exc}"}
    dump_json(result)
    return 0 if result.get("decision") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
