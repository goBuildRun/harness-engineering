#!/usr/bin/env python3
"""Bounded digest-first context receipt for Story phase transitions."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ael_runtime import canonical_digest, now
from ael_timing import budget_status, stage_budget_status


SCHEMA = "harness-phase-handoff-v1"
MAX_CONTEXT_CHARS = 6000
MAX_TOOL_CALLS_PER_BATCH = 8


def _file_binding(path: Path) -> dict[str, str | int]:
    try:
        content = path.read_bytes()
    except OSError:
        return {"ref": path.name, "sha256": "absent", "chars": 0}
    return {
        "ref": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "chars": len(content.decode("utf-8", errors="replace")),
    }


def build(result: dict[str, Any], task_root: Path, to_stage: str) -> dict[str, Any]:
    cycle = result.get("cycle") or {}
    root_budget = budget_status(cycle)
    stage_budget = stage_budget_status(cycle, to_stage)
    passed = [
        name for name, state in (cycle.get("stages") or {}).items()
        if isinstance(state, dict) and state.get("status") == "pass"
    ]
    payload = {
        "schema": SCHEMA,
        "task_id": str(result.get("task_id") or ""),
        "from_stage": passed[-1] if passed else "confirmation",
        "to_stage": to_stage,
        "created_at": now(),
        "root_budget": {
            key: root_budget.get(key)
            for key in ("decision", "reason", "budget_ms", "elapsed_ms", "remaining_ms")
        },
        "stage_budget": {
            key: stage_budget.get(key)
            for key in ("decision", "reason", "budget_ms", "elapsed_ms", "remaining_ms", "attempt")
        },
        "candidate_digest": str((result.get("candidate") or {}).get("digest") or "pending"),
        "evidence_digest": str(
            (result.get("evidence_manifest") or {}).get("evidence_digest") or "pending"
        ),
        "context_index": _file_binding(task_root / "context_index.json"),
        "admission": {
            "mode": "digest-first",
            "max_context_chars": MAX_CONTEXT_CHARS,
            "max_tool_calls_per_batch": MAX_TOOL_CALLS_PER_BATCH,
            "full_artifacts_included": False,
            "host_phase_session": "required-but-guarded",
        },
        "blocker_codes": sorted({str(item) for item in result.get("blockers") or []}),
    }
    payload["handoff_digest"] = canonical_digest({
        key: value for key, value in payload.items() if key != "created_at"
    })
    return payload


def write(result: dict[str, Any], task_root: Path, to_stage: str) -> dict[str, Any]:
    payload = build(result, task_root, to_stage)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(raw) > MAX_CONTEXT_CHARS:
        raise ValueError("PHASE_HANDOFF_CONTEXT_LIMIT_EXCEEDED")
    path = task_root / "phase-handoff.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return payload
