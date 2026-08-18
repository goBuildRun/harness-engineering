#!/usr/bin/env python3
"""Check task-scoped tracked and untracked files for Git whitespace errors."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Any

from harness_output import dump_json
from plan_sync_check import active_task_baseline
from worktree_baseline import changed_since_baseline
from workspace_paths import load_layout


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for name in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH",
    ):
        env.pop(name, None)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        check=False,
    )


def _untracked_paths(repo: Path) -> set[str]:
    completed = _git(
        repo, "-c", "core.quotePath=false", "ls-files", "--others",
        "--exclude-standard", "-z",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git ls-files failed")
    return {path for path in completed.stdout.split("\0") if path}


def changed_for_layout(layout: Any) -> list[str]:
    _, baseline = active_task_baseline(layout)
    if baseline is not None and baseline.is_file():
        return changed_since_baseline(layout.product_root, baseline)

    found: list[str] = []
    for args in (
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        completed = _git(layout.product_root, *args)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
        found.extend(path for path in completed.stdout.split("\0") if path)
    return list(dict.fromkeys(found))


def _safe_file(repo: Path, relative: str) -> Path | None:
    path = repo / relative
    try:
        path.resolve().relative_to(repo.resolve())
    except ValueError:
        return None
    return path if path.is_file() and not path.is_symlink() else None


def _summary(outputs: list[str]) -> str:
    lines = [line.strip() for output in outputs for line in output.splitlines() if line.strip()]
    return "; ".join(lines[:20])[:2000]


def _head_exists(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--verify", "HEAD").returncode == 0


def _commit_base(repo: Path, commit: str) -> str | None:
    completed = _git(repo, "rev-parse", f"{commit}^1")
    return completed.stdout.strip() if completed.returncode == 0 else None


def commit_changed(repo: Path, commit: str) -> list[str]:
    base = _commit_base(repo, commit)
    args = (
        ("-c", "core.quotePath=false", "diff", "--name-only", "-z", base, commit)
        if base
        else (
            "-c", "core.quotePath=false", "diff-tree", "--root", "--name-only",
            "-z", "--no-commit-id", "-r", commit,
        )
    )
    completed = _git(repo, *args)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git diff --name-only failed")
    return [path for path in completed.stdout.split("\0") if path]


def check_diff_integrity(repo: Path, changed: list[str]) -> dict[str, Any]:
    repo = repo.resolve()
    try:
        untracked = _untracked_paths(repo)
    except RuntimeError as exc:
        return {"decision": "block", "reason": f"DIFF_INTEGRITY_GIT_ERROR: {exc}"}

    scoped = sorted(set(changed))
    tracked = [path for path in scoped if path not in untracked]
    findings: list[str] = []

    if tracked:
        commands = (
            [("diff", "--check", "HEAD", "--", *tracked)]
            if _head_exists(repo)
            else [
                ("diff", "--cached", "--check", "--", *tracked),
                ("diff", "--check", "--", *tracked),
            ]
        )
        for args in commands:
            completed = _git(repo, *args)
            if completed.stdout.strip():
                findings.append(completed.stdout)
            if completed.returncode != 0 and not completed.stdout.strip():
                detail = completed.stderr.strip() or f"git {' '.join(args[:3])} exited {completed.returncode}"
                return {"decision": "block", "reason": f"DIFF_INTEGRITY_GIT_ERROR: {detail}"}

    checked_untracked = 0
    for relative in sorted(untracked.intersection(scoped)):
        path = _safe_file(repo, relative)
        if path is None:
            continue
        checked_untracked += 1
        completed = _git(
            repo, "diff", "--no-index", "--check", "--", os.devnull, relative,
        )
        if completed.stdout.strip():
            findings.append(completed.stdout)
        elif completed.returncode not in {0, 1}:
            detail = completed.stderr.strip() or f"git diff --no-index --check exited {completed.returncode}"
            return {"decision": "block", "reason": f"DIFF_INTEGRITY_GIT_ERROR: {detail}"}

    if findings:
        return {
            "decision": "block",
            "reason": f"DIFF_INTEGRITY_FAILED: {_summary(findings)}",
            "checked_paths": len(scoped),
            "checked_untracked": checked_untracked,
        }
    return {
        "decision": "pass",
        "reason": (
            f"DIFF_INTEGRITY_OK: {len(scoped)} task-scoped paths; "
            f"untracked={checked_untracked}"
        ),
        "checked_paths": len(scoped),
        "checked_untracked": checked_untracked,
    }


def check_commit_integrity(repo: Path, commit: str) -> dict[str, Any]:
    repo = repo.resolve()
    try:
        changed = commit_changed(repo, commit)
    except RuntimeError as exc:
        return {"decision": "block", "reason": f"DIFF_INTEGRITY_GIT_ERROR: {exc}"}
    base = _commit_base(repo, commit)
    args = (
        ("diff", "--check", base, commit, "--", *changed)
        if base
        else (
            "diff-tree", "--root", "--check", "--no-commit-id", "-r", commit,
            "--", *changed,
        )
    )
    completed = _git(repo, *args)
    if completed.stdout.strip():
        return {
            "decision": "block",
            "reason": f"DIFF_INTEGRITY_FAILED: {_summary([completed.stdout])}",
            "checked_paths": len(changed),
            "checked_untracked": 0,
        }
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"git diff --check exited {completed.returncode}"
        return {"decision": "block", "reason": f"DIFF_INTEGRITY_GIT_ERROR: {detail}"}
    return {
        "decision": "pass",
        "reason": f"DIFF_INTEGRITY_OK: commit={commit}; paths={len(changed)}",
        "checked_paths": len(changed),
        "checked_untracked": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--product-root", default=os.environ.get("HARNESS_PRODUCT_ROOT", ""))
    parser.add_argument("--product-id", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args()

    layout = load_layout(
        Path(args.harness_root).resolve(),
        Path(args.product_root).resolve() if args.product_root else None,
        args.product_id,
    )
    if args.commit:
        result = check_commit_integrity(layout.product_root, args.commit)
    else:
        try:
            changed = changed_for_layout(layout)
        except (RuntimeError, ValueError) as exc:
            result = {"decision": "block", "reason": f"DIFF_INTEGRITY_BASELINE_INVALID: {exc}"}
        else:
            result = check_diff_integrity(layout.product_root, changed)
    dump_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
