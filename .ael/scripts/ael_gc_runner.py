#!/usr/bin/env python3
"""Bounded independent GC subprocess orchestration."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ael_gc_contract import telemetry_add_gc, valid_gc_result
from ael_gc_context import build_gc_context
from process_control import run_process_group


def read_gc_result(task_root: Path) -> dict[str, Any] | None:
    path = task_root / "gc_result.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"decision": "block", "reason": "GC_RESULT_INVALID"}
    return (
        value if isinstance(value, dict)
        else {"decision": "block", "reason": "GC_RESULT_INVALID"}
    )


def invoke_gc_once(
    result: dict[str, Any], task_root: Path, mechanical: dict[str, Any],
    changed: list[str], repo: Path, *, timeout_ms: int | None = None,
) -> dict[str, Any] | None:
    existing = read_gc_result(task_root)
    if valid_gc_result(existing, result, mechanical.get("subject_digest", ""),
                       result.get("policy_digest", ""), mechanical=mechanical):
        return existing
    if existing and existing.get("reason") == "GC_RESULT_INVALID":
        result["blockers"] = sorted(
            set(result.get("blockers", [])) | {"GC_RESULT_INVALID"}
        )
        return existing
    argv_json = os.environ.get("AEL_GC_AGENT_ARGV", "").strip()
    if not argv_json:
        return None
    if result["cost"]["harness"].get("agent_calls", 0) >= 1:
        result["blockers"] = sorted(set(result.get("blockers", [])) | {"BUDGET_APPROVAL_REQUIRED"})
        return None
    try:
        argv = json.loads(argv_json)
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        result["blockers"] = sorted(set(result.get("blockers", [])) | {"GC_RUNNER_INVALID"})
        return None
    try:
        context, context_chars = build_gc_context(result, mechanical, changed, repo)
    except ValueError:
        result["blockers"] = sorted(
            set(result.get("blockers", [])) | {"BUDGET_APPROVAL_REQUIRED"})
        return None
    context_path = task_root / "gc_context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    started = time.monotonic()
    try:
        completed = run_process_group(
            [*argv, str(context_path), str(task_root / "gc_result.json")],
            cwd=repo, env=os.environ.copy(),
            timeout=max(0.001, timeout_ms / 1000) if timeout_ms is not None else 30.0,
        )
    except subprocess.TimeoutExpired:
        telemetry_add_gc(result, context_chars=context_chars,
                         duration_ms=int((time.monotonic() - started) * 1000))
        result["blockers"] = sorted(set(result.get("blockers", [])) | {"GC_TIMEOUT"})
        return None
    except OSError:
        telemetry_add_gc(result, context_chars=context_chars,
                         duration_ms=int((time.monotonic() - started) * 1000))
        result["blockers"] = sorted(
            set(result.get("blockers", [])) | {"GC_RUNNER_INVALID"}
        )
        return None
    telemetry_add_gc(result, context_chars=context_chars,
                     duration_ms=int((time.monotonic() - started) * 1000))
    if completed.returncode != 0:
        result["blockers"] = sorted(set(result.get("blockers", [])) | {"GC_REQUIRED"})
        return None
    return read_gc_result(task_root)
