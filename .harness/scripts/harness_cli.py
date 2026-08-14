#!/usr/bin/env python3
"""CLI assembly for the lean Harness runtime."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from harness_commands import cmd_amend, cmd_ci_check, cmd_finish, cmd_start, cmd_status, cmd_usage_baseline
from harness_assurance import create_bootstrap, create_release_candidate
from harness_runtime import load_result, resolve_task_id, result_path
from harness_migration_commands import cmd_audit, cmd_migrate
from harness_runtime import TIERS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness")
    parser.add_argument("--harness-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--product-root", default=os.environ.get("HARNESS_PRODUCT_ROOT", os.getcwd()))
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("task_id", nargs="?")
    start.add_argument("--work-item", default="")
    start.add_argument("--tier", choices=tuple(TIERS), default="standard")
    start.add_argument("--scope", action="append", default=[])
    start.add_argument(
        "--kind", choices=("implementation", "debt-maintenance", "scope-change", "hotfix", "harness-maintenance"),
        default="implementation",
    )
    start.add_argument("--reason", default="")
    sub.add_parser("status").add_argument("task_id", nargs="?")
    finish = sub.add_parser("finish")
    finish.add_argument("task_id", nargs="?")
    finish.add_argument("--skip-legacy-gates", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("workspace").add_argument("action", choices=("audit",))
    migrate = sub.add_parser("migrate-task")
    migrate.add_argument("task_id")
    migrate.add_argument("--reason", required=True)
    amend = sub.add_parser("amend-task")
    amend.add_argument("task_id")
    amend.add_argument("--scope", action="append", required=True)
    amend.add_argument("--reason", required=True)
    amend.add_argument("--kind", choices=("implementation", "debt-maintenance", "scope-change", "hotfix", "harness-maintenance"), default="")
    usage = sub.add_parser("usage-baseline")
    usage.add_argument("task_id")
    usage.add_argument("--reason", required=True)
    usage.add_argument("--epic-id", default="")
    bootstrap = sub.add_parser("bootstrap-guarded")
    bootstrap.add_argument("--task-id", required=True)
    bootstrap.add_argument("--reason", required=True)
    sub.add_parser("release-candidate")
    ci = sub.add_parser("ci-check")
    ci.add_argument("--task-id", required=True)
    ci.add_argument("--commit", required=True)
    ci.add_argument("--tier", choices=tuple(TIERS), default="standard")
    ci.add_argument("--scope", action="append", required=True)
    ci.add_argument("--gc-result", default="")
    ci.add_argument("--output", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "start": cmd_start, "status": cmd_status, "finish": cmd_finish,
        "workspace": cmd_audit, "migrate-task": cmd_migrate, "amend-task": cmd_amend,
        "usage-baseline": cmd_usage_baseline,
        "ci-check": cmd_ci_check,
    }
    if args.command == "bootstrap-guarded":
        import json
        outcome = create_bootstrap(Path(args.product_root).resolve(), args.task_id, args.reason)
        print(json.dumps(outcome, ensure_ascii=False))
        return 0
    if args.command == "release-candidate":
        import json
        try:
            product_root = Path(args.product_root).resolve()
            task_id, blockers = resolve_task_id(product_root, None)
            if blockers or not task_id:
                raise ValueError("active task result is unavailable")
            result = load_result(result_path(product_root, task_id))
        except (OSError, ValueError, json.JSONDecodeError):
            outcome = {"decision": "block", "reason": "RELEASE_CANDIDATE_ACTIVE_RESULT_MISSING"}
        else:
            outcome = create_release_candidate(Path(args.product_root).resolve(), result)
        print(json.dumps(outcome, ensure_ascii=False))
        return 0
    return handlers[args.command](args)
