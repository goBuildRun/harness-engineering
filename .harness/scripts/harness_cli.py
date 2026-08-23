#!/usr/bin/env python3
"""CLI assembly for the lean Harness runtime."""
from __future__ import annotations

import argparse
import os
import json
from pathlib import Path

from harness_commands import cmd_amend, cmd_ci_check, cmd_finish, cmd_start, cmd_status
from harness_cycle_commands import cmd_confirm, cmd_release_ready, cmd_stage, cmd_usage_baseline
from harness_assurance import create_bootstrap, create_release_candidate
from harness_runtime import load_result, resolve_task_id, result_path
from harness_migration_commands import cmd_audit, cmd_migrate
from harness_runtime import TIERS
from harness_output import decision_exit_code, dump_json, reset_decision
from harness_planning import plan_batch
from workspace_paths import load_layout


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
    plan = sub.add_parser("plan", help="batch BMAD planning and task-scoped receipts")
    plan.add_argument("--level", choices=("L1", "L2", "L3"), required=True)
    plan.add_argument("--task-dir", action="append", required=True)
    plan.add_argument("--batch-id", default="")
    plan.add_argument("--provider-mode", choices=("offline", "configured"), default="offline")
    confirm = sub.add_parser("confirm")
    confirm.add_argument("task_id")
    confirm.add_argument("--work-item", default="")
    confirm.add_argument("--provider", default="")
    confirm.add_argument("--tier", choices=tuple(TIERS), default="strict")
    confirm.add_argument("--scope", action="append", default=[])
    confirm.add_argument(
        "--kind", choices=("implementation", "debt-maintenance", "scope-change", "hotfix", "harness-maintenance"),
        default="implementation",
    )
    sub.add_parser("status").add_argument("task_id", nargs="?")
    finish = sub.add_parser("finish")
    finish.add_argument("task_id", nargs="?")
    finish.add_argument("--skip-legacy-gates", action="store_true", help=argparse.SUPPRESS)
    release_ready = sub.add_parser("release-ready")
    release_ready.add_argument("task_id")
    release_ready.add_argument("--commit", required=True)
    release_ready.add_argument("--receipt", default="")
    sub.add_parser("workspace").add_argument("action", choices=("audit",))
    migrate = sub.add_parser("migrate-task")
    migrate.add_argument("task_id")
    migrate.add_argument("--reason", required=True)
    amend = sub.add_parser("amend-task")
    amend.add_argument("task_id")
    amend.add_argument(
        "--scope",
        action="append",
        required=True,
        help="complete replacement scope; repeat --scope for every path that must remain bound",
    )
    amend.add_argument("--reason", required=True)
    amend.add_argument("--kind", choices=("implementation", "debt-maintenance", "scope-change", "hotfix", "harness-maintenance"), default="")
    usage = sub.add_parser("usage-baseline")
    usage.add_argument("task_id")
    usage.add_argument("--reason", required=True)
    usage.add_argument("--epic-id", default="")
    stage = sub.add_parser("stage")
    stage.add_argument("task_id")
    stage.add_argument("action", choices=("start", "end", "status"))
    stage.add_argument("stage", nargs="?", choices=(
        "takeover", "planning", "implementation_test", "independent_qa",
        "deploy_provider", "finalize",
    ))
    stage.add_argument("--decision", choices=("pass", "block"), default="pass")
    stage.add_argument("--reason", default="STAGE_COMPLETED")
    stage.add_argument("--tool-wait-ms", default="unknown")
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
    reset_decision()
    args = build_parser().parse_args()
    handlers = {
        "confirm": cmd_confirm, "start": cmd_start, "status": cmd_status,
        "finish": cmd_finish, "release-ready": cmd_release_ready,
        "workspace": cmd_audit, "migrate-task": cmd_migrate, "amend-task": cmd_amend,
        "usage-baseline": cmd_usage_baseline,
        "stage": cmd_stage,
        "ci-check": cmd_ci_check,
    }
    if args.command == "plan":
        layout = load_layout(Path(args.harness_root).resolve(), Path(args.product_root).resolve())
        outcome = plan_batch(
            layout, level=args.level, task_dirs=args.task_dir,
            batch_id=args.batch_id, provider_mode=args.provider_mode,
        )
        dump_json(outcome)
        return decision_exit_code()
    if args.command == "bootstrap-guarded":
        outcome = create_bootstrap(Path(args.product_root).resolve(), args.task_id, args.reason)
        dump_json(outcome)
        return decision_exit_code()
    if args.command == "release-candidate":
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
        dump_json(outcome)
        return decision_exit_code()
    fallback = handlers[args.command](args)
    return decision_exit_code(fallback)
