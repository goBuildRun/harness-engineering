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
        "guarded": "git-hooks",
        "enforced": "git-receive",
    }
    return {
        "level": level,
        "task_execution": "incomplete",
        "acceptance_authority": authorities[level],
        "bypassable": level != "enforced",
        "verified_at": "",
        "blockers": list(blockers or []),
    }


def guard_prelude() -> str:
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
"""


def pre_commit_script() -> str:
    return guard_prelude() + """\
OUTPUT="$("$HARNESS_BIN" --product-root "$PRODUCT_ROOT" status 2>&1)" || {
  echo "$OUTPUT" >&2
  exit 1
}
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); r=d.get("result",{}); ok=d.get("decision")=="pass" and r.get("decision")=="pass" and r.get("state")=="validated"; print("HARNESS_GUARD_PASS" if ok else "HARNESS_GUARD_BLOCKED", file=sys.stderr); raise SystemExit(0 if ok else 1)' "$OUTPUT"
"""


def pre_push_script() -> str:
    return guard_prelude() + """\
ATTEST="$CONFIGURED_ROOT/.harness/scripts/harness_attestation.py"
REMOTE_NAME="${1:-origin}"
ATTEST_REFS=()
while read -r local_ref local_sha remote_ref remote_sha; do
  [[ "$local_sha" =~ ^0+$ ]] && continue
  if [[ "$remote_sha" =~ ^0+$ ]]; then
    ACTIVE="$PRODUCT_ROOT/harness-workspace/runs/active_task.json"
    [[ -f "$ACTIVE" ]] || { echo 'HARNESS_GUARD_ACTIVE_TASK_MISSING' >&2; exit 1; }
    TASK_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("task_id", ""))' "$ACTIVE")"
    BASELINE="$PRODUCT_ROOT/harness-workspace/runs/tasks/$TASK_ID/worktree_baseline.json"
    BASE_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("repo_head", ""))' "$BASELINE")"
    [[ -n "$BASE_SHA" ]] || { echo 'HARNESS_GUARD_BASELINE_MISSING' >&2; exit 1; }
    COMMITS="$(git rev-list --reverse "$BASE_SHA..$local_sha")"
  else
    COMMITS="$(git rev-list --reverse "$remote_sha..$local_sha")"
  fi
  while read -r commit; do
    [[ -z "$commit" ]] && continue
    python3 "$ATTEST" verify --repo "$PRODUCT_ROOT" --commit "$commit" >/dev/null || {
      echo "HARNESS_PUSH_GUARD_BLOCKED: $commit has no valid attestation" >&2
      exit 1
    }
    ATTEST_REFS+=("refs/harness/attestations/$commit:refs/harness/attestations/$commit")
    ATTEST_REFS+=("refs/harness/results/$commit:refs/harness/results/$commit")
    if git show-ref --verify --quiet "refs/harness/gc/$commit"; then
      ATTEST_REFS+=("refs/harness/gc/$commit:refs/harness/gc/$commit")
    fi
  done <<< "$COMMITS"
done
if (( ${#ATTEST_REFS[@]} )); then
  git push --atomic --no-verify "$REMOTE_NAME" "${ATTEST_REFS[@]}" >/dev/null || {
    echo 'HARNESS_PUSH_GUARD_BLOCKED: attestation refs were not accepted atomically' >&2
    exit 1
  }
fi
echo 'HARNESS_PUSH_GUARD_PASS' >&2
"""


def post_commit_script() -> str:
    return guard_prelude() + """\
ACTIVE="$PRODUCT_ROOT/harness-workspace/runs/active_task.json"
[[ -f "$ACTIVE" ]] || exit 0
TASK_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("task_id", ""))' "$ACTIVE")"
RESULT="$PRODUCT_ROOT/harness-workspace/runs/tasks/$TASK_ID/result.json"
[[ -f "$RESULT" ]] || exit 0
python3 "$CONFIGURED_ROOT/.harness/scripts/harness_attestation.py" create \
  --repo "$PRODUCT_ROOT" --commit HEAD --result "$RESULT" >/dev/null || {
  echo 'HARNESS_ATTESTATION_CREATE_FAILED: run harness finish before commit' >&2
  exit 1
}
echo 'HARNESS_ATTESTATION_CREATED' >&2
"""


def install_guards(product: Path) -> dict[str, Any]:
    git_dir = product / ".git"
    if not git_dir.exists():
        return {"decision": "block", "reason": "GUARDED_GIT_REPOSITORY_REQUIRED"}
    hooks = product / ".githooks"
    hooks.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    scripts = {"pre-commit": pre_commit_script(), "post-commit": post_commit_script(),
               "pre-push": pre_push_script()}
    for name, text in scripts.items():
        path = hooks / name
        path.write_text(text, encoding="utf-8")
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
    expected = {"pre-commit": pre_commit_script(), "post-commit": post_commit_script(),
                "pre-push": pre_push_script()}
    missing = [name for name in expected if not os.access(product / ".githooks" / name, os.X_OK)]
    mismatched = [name for name, text in expected.items()
                  if (product / ".githooks" / name).is_file()
                  and (product / ".githooks" / name).read_text(encoding="utf-8") != text]
    try:
        tracked_output = subprocess.check_output(
            ["git", "ls-files", "--", ".githooks/pre-commit", ".githooks/post-commit",
             ".githooks/pre-push"],
            cwd=product, text=True, stderr=subprocess.DEVNULL,
        ).splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError):
        tracked_output = []
    untracked_hooks = sorted(set(f".githooks/{name}" for name in expected) - set(tracked_output))
    runtime = Path(runtime_root) / ".harness" / "scripts" / "harness"
    valid = (configured == ".githooks" and not missing and not mismatched
             and not untracked_hooks and runtime.is_file())
    blockers = []
    if configured != ".githooks" or missing or not runtime.is_file():
        blockers.append("GUARDED_GIT_GUARDS_MISSING")
    if mismatched:
        blockers.append("GUARDED_GIT_GUARDS_MODIFIED")
    if untracked_hooks:
        blockers.append("GUARDED_GIT_GUARDS_UNTRACKED")
    return {
        "level": "guarded" if valid else "local",
        "configured_hooks_path": configured,
        "runtime_configured": bool(runtime_root),
        "missing_hooks": missing,
        "mismatched_hooks": mismatched,
        "untracked_hooks": untracked_hooks,
        "bypassable": True,
        "blockers": blockers,
    }


def refresh_result(result: dict[str, Any], product: Path, policy_digest: str, *,
                   phase: str, verified_at: str, attestation: dict[str, Any]) -> None:
    guards = audit_guards(product)
    level = "guarded" if guards["level"] == "guarded" else "local"
    result["assurance"].update(
        level=level, acceptance_authority="git-hooks" if level == "guarded" else "worktree",
        bypassable=True, verified_at=verified_at, blockers=guards["blockers"],
        guard_audit=guards, head_attestation={**attestation, "phase": phase},
    )
    result["enforcement"] = "shadow"
    result["enforcement_notice"] = "GUARDED" if level == "guarded" else "LOCAL_ONLY"
    sync_task_execution(result)
