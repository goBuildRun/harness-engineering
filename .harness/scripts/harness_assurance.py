#!/usr/bin/env python3
"""Assurance snapshots and versioned Git guards for product repositories."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any


ASSURANCE_LEVELS = {"local", "guarded", "enforced"}


def snapshot(level: str = "local", *, blockers: list[str] | None = None) -> dict[str, Any]:
    if level not in ASSURANCE_LEVELS:
        level = "local"
    authorities = {
        "local": "worktree",
        "guarded": "git-guards+ci",
        "enforced": "protected-authority",
    }
    return {
        "level": level,
        "task_execution": "incomplete",
        "acceptance_authority": authorities[level],
        "bypassable": level != "enforced",
        "verified_at": "",
        "blockers": list(blockers or []),
    }


def apply_enforcement(result: dict[str, Any], enforcement: dict[str, Any], *, guarded: bool) -> None:
    enforced = enforcement.get("enforcement") == "enforced"
    level = "enforced" if enforced else ("guarded" if guarded else "local")
    assurance = snapshot(level, blockers=list(enforcement.get("blockers") or []))
    assurance["task_execution"] = (
        "complete" if result.get("state") == "validated" and result.get("decision") == "pass"
        else "incomplete"
    )
    assurance["verified_at"] = str(enforcement.get("probed_at") or "")
    result["assurance"] = assurance
    result["enforcement"] = "enforced" if enforced else "shadow"


def refresh_result(result: dict[str, Any], product: Path, enforcement: dict[str, Any]) -> None:
    guards = audit_guards(product)
    apply_enforcement(result, enforcement, guarded=guards["level"] == "guarded")
    result["enforcement_notice"] = enforcement["notice"]
    result["enforcement_probe"] = enforcement
    result["assurance"]["guard_audit"] = guards


def audit_report(product: Path, enforcement: dict[str, Any]) -> dict[str, Any]:
    guards = audit_guards(product)
    enforced = enforcement["enforcement"] == "enforced"
    return {
        "level": "enforced" if enforced else guards["level"],
        "acceptance_authority": (
            "protected-authority" if enforced
            else ("git-guards+ci" if guards["level"] == "guarded" else "worktree")
        ),
        "bypassable": not enforced,
        "guard_audit": guards,
    }


def guard_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
PRODUCT_ROOT="$(git rev-parse --show-toplevel)"
CONFIGURED_ROOT="$(git config --local --get harness.engineeringRoot || true)"
HARNESS_BIN="${HARNESS_ENGINEERING_ROOT:-$CONFIGURED_ROOT}/.harness/scripts/harness"
if [[ ! -x "$HARNESS_BIN" ]]; then
  HARNESS_BIN="$(command -v harness || true)"
fi
if [[ -z "$HARNESS_BIN" || ! -x "$HARNESS_BIN" ]]; then
  echo 'HARNESS_GUARD_RUNTIME_MISSING: set HARNESS_ENGINEERING_ROOT or install harness on PATH' >&2
  exit 1
fi
OUTPUT="$($HARNESS_BIN --product-root "$PRODUCT_ROOT" status 2>&1)" || {
  echo "$OUTPUT" >&2
  exit 1
}
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); r=d.get("result",{}); ok=d.get("decision")=="pass" and r.get("decision")=="pass" and r.get("state")=="validated"; print("HARNESS_GUARD_PASS" if ok else "HARNESS_GUARD_BLOCKED", file=sys.stderr); raise SystemExit(0 if ok else 1)' "$OUTPUT"
"""


def install_guards(product: Path) -> dict[str, Any]:
    git_dir = product / ".git"
    if not git_dir.exists():
        return {"decision": "block", "reason": "GUARDED_GIT_REPOSITORY_REQUIRED"}
    hooks = product / ".githooks"
    hooks.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for name in ("pre-commit", "pre-push"):
        path = hooks / name
        path.write_text(guard_script(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        created.append(str(path.relative_to(product)))
    try:
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", ".githooks"],
            cwd=product, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        subprocess.run(
            ["git", "config", "--local", "harness.engineeringRoot", str(Path(__file__).resolve().parents[2])],
            cwd=product, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        return {"decision": "block", "reason": f"GUARDED_GIT_CONFIG_FAILED: {exc}"}
    return {"decision": "pass", "reason": "GUARDED_GIT_GUARDS_INSTALLED", "created": created}


def configure(product: Path, level: str) -> dict[str, Any]:
    if level == "guarded":
        return install_guards(product)
    return {"decision": "pass", "reason": "LOCAL_ASSURANCE_SELECTED"}


def mark_local(result: dict[str, Any]) -> None:
    result["enforcement"] = "shadow"
    result["assurance"].update(
        level="local", acceptance_authority="worktree", bypassable=True,
    )


def sync_task_execution(result: dict[str, Any]) -> None:
    result["assurance"]["task_execution"] = (
        "complete" if result.get("state") == "validated" and result.get("decision") == "pass"
        else "incomplete"
    )


def finalize(result: dict[str, Any], decision_fn: Any, *, local: bool = False) -> None:
    decision_fn(result)
    if local:
        mark_local(result)
    sync_task_execution(result)


def audit_guards(product: Path) -> dict[str, Any]:
    try:
        configured = subprocess.check_output(
            ["git", "config", "--local", "--get", "core.hooksPath"],
            cwd=product, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        configured = ""
    try:
        runtime_root = subprocess.check_output(
            ["git", "config", "--local", "--get", "harness.engineeringRoot"],
            cwd=product, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        runtime_root = ""
    missing = [name for name in ("pre-commit", "pre-push") if not os.access(product / ".githooks" / name, os.X_OK)]
    runtime = Path(runtime_root) / ".harness" / "scripts" / "harness"
    valid = configured == ".githooks" and not missing and runtime.is_file()
    return {
        "level": "guarded" if valid else "local",
        "configured_hooks_path": configured,
        "runtime_configured": bool(runtime_root),
        "missing_hooks": missing,
        "bypassable": True,
        "blockers": [] if valid else ["GUARDED_GIT_GUARDS_MISSING"],
    }
