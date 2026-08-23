"""Lifecycle wrapper helpers for finish policy restoration."""
from __future__ import annotations

import argparse
import json
import os
from typing import Callable


def execute_finish(
    args: argparse.Namespace, *, commands, finish_locked: Callable[..., int],
) -> int:
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
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = {}
    task_scoped = bool(isinstance(stored, dict) and stored.get("task_scoped_state"))
    previous_scope = os.environ.get("HARNESS_TASK_SCOPED")
    previous_task_id = os.environ.get("HARNESS_TASK_ID")
    previous_lean = os.environ.get("HARNESS_LEAN_FLOW")
    if task_scoped:
        os.environ["HARNESS_TASK_SCOPED"] = "1"
        os.environ["HARNESS_TASK_ID"] = task_id
    flow_policy = stored.get("flow_policy") if isinstance(stored, dict) else None
    if "flow_policy" in stored and (
        not isinstance(flow_policy, dict)
        or not isinstance(flow_policy.get("lean"), bool)
        or not isinstance(flow_policy.get("growth_release"), bool)
        or not str(flow_policy.get("source") or "").strip()
    ):
        commands.dump_json({"decision": "block", "reason": "FLOW_POLICY_INVALID"})
        return 0
    if isinstance(flow_policy, dict):
        if flow_policy["lean"]:
            os.environ["HARNESS_LEAN_FLOW"] = "1"
        else:
            os.environ.pop("HARNESS_LEAN_FLOW", None)
    lock_path = path if task_scoped else commands.active_task_path(product)
    try:
        if lock_path == path:
            with commands.task_operation_lock(path):
                return finish_locked(args, product, harness, task_id, path, started, commands=commands)
        with commands.task_operation_lock(lock_path), commands.task_operation_lock(path):
            return finish_locked(args, product, harness, task_id, path, started, commands=commands)
    finally:
        if previous_scope is None:
            os.environ.pop("HARNESS_TASK_SCOPED", None)
        else:
            os.environ["HARNESS_TASK_SCOPED"] = previous_scope
        if previous_task_id is None:
            os.environ.pop("HARNESS_TASK_ID", None)
        else:
            os.environ["HARNESS_TASK_ID"] = previous_task_id
        if previous_lean is None:
            os.environ.pop("HARNESS_LEAN_FLOW", None)
        else:
            os.environ["HARNESS_LEAN_FLOW"] = previous_lean
