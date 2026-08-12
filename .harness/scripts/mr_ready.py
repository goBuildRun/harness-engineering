#!/usr/bin/env python3
"""Product MR readiness gate for harness-engineering."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from harness_output import dump_json
from agent_review import build_checklist, load_and_validate_receipt

from workspace_paths import load_layout


def emit(decision: str, reason: str, **extra: Any) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def run(argv: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip()


def run_json(argv: list[str], cwd: Path) -> dict[str, Any]:
    code, out = run(argv, cwd)
    if code != 0:
        return {"decision": "block", "reason": f"COMMAND_FAILED: {argv[0]} exit={code}", "log": out[-2000:]}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"decision": "block", "reason": f"COMMAND_NON_JSON: {argv[0]}", "log": out[-2000:]}


def git_changed_files(product_root: Path) -> list[str]:
    code, out = run(["git", "status", "--porcelain=v1"], product_root)
    if code != 0:
        return []
    files: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        files.append(line[3:].strip())
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--message", default="chore: agent completed task")
    parser.add_argument("--allow-no-changes", action="store_true")
    parser.add_argument("--agent-review-receipt", default=os.environ.get("HARNESS_AGENT_REVIEW_RECEIPT", ""))
    parser.add_argument(
        "--require-agent-review", action="store_true",
        default=os.environ.get("HARNESS_AGENT_REVIEW_REQUIRED", "").lower() == "true",
    )
    args = parser.parse_args()

    product_root_arg = args.product_root
    layout = load_layout(
        Path(args.harness_root).resolve(),
        Path(product_root_arg).resolve() if product_root_arg else None,
    )
    product_root = layout.product_root
    harness_root = layout.harness_root

    code, inside = run(["git", "rev-parse", "--is-inside-work-tree"], product_root)
    if code != 0 or inside.strip() != "true":
        emit("block", f"MR_PRODUCT_NOT_GIT_REPO: {product_root}")
        return 0

    changed = git_changed_files(product_root)
    runs_prefix = f"{layout.rel(layout.runs_root).rstrip('/')}/"
    runs_changes = [p for p in changed if p.startswith(runs_prefix)]
    if runs_changes:
        emit("block", "MR_RUNS_STATE_DIRTY: harness-workspace/runs 是本地状态，不应进入 MR", files=runs_changes)
        return 0
    if not changed and not args.allow_no_changes:
        emit("block", "MR_NO_CHANGES: 产品仓库没有待提交变更")
        return 0

    check = run_json(["bash", str(harness_root / ".harness/scripts/check.sh")], harness_root)
    if check.get("decision") != "pass":
        emit("block", "MR_CHECK_FAILED", check=check, product=str(product_root))
        return 0

    review_checklist = build_checklist(product_root, changed)
    review_receipt = None
    if args.agent_review_receipt:
        receipt_path = Path(args.agent_review_receipt).resolve()
        try:
            receipt_path.relative_to(layout.runs_root.resolve())
        except ValueError:
            emit("block", "AGENT_REVIEW_RECEIPT_OUTSIDE_RUNS", review_checklist=review_checklist)
            return 0
        review_receipt, review_error = load_and_validate_receipt(
            receipt_path, review_checklist,
        )
        if review_error:
            emit("block", review_error, review_checklist=review_checklist)
            return 0
    if args.require_agent_review and review_receipt is None:
        emit("block", "AGENT_REVIEW_REQUIRED", review_checklist=review_checklist)
        return 0

    _code, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], product_root)
    emit(
        "pass",
        "MR_READY: 产品根变更、Planning Gate、DAG、QA、quality 全链已通过，可创建 MR",
        product=str(product_root),
        branch=branch,
        message=args.message,
        changed_files=changed,
        review_checklist=review_checklist,
        review_receipt=review_receipt,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
