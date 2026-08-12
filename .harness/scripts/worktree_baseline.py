#!/usr/bin/env python3
"""Capture and compare per-Work-Item dirty worktree baselines."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "harness-worktree-baseline-v1"
_GENERATED_BYTECODE_SUFFIXES = {".pyc", ".pyo"}


def _is_generated_cache(path: str) -> bool:
    parts = Path(path).parts
    return "__pycache__" in parts or Path(path).suffix in _GENERATED_BYTECODE_SUFFIXES


def _git_paths(repo: Path, args: list[str]) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [os.fsdecode(item) for item in result.stdout.split(b"\0") if item]


def _head(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _raw_dirty_kinds(repo: Path) -> dict[str, set[str]]:
    kinds: dict[str, set[str]] = defaultdict(set)
    commands = (
        ("unstaged", ["diff", "--no-renames", "--name-only", "-z"]),
        ("staged", ["diff", "--cached", "--no-renames", "--name-only", "-z"]),
        ("untracked", ["ls-files", "--others", "--exclude-standard", "-z"]),
    )
    for kind, args in commands:
        for path in _git_paths(repo, args):
            if _is_generated_cache(path):
                continue
            kinds[path].add(kind)
    return kinds


def _dirty_kinds(repo: Path) -> dict[str, set[str]]:
    kinds = _raw_dirty_kinds(repo)
    expanded: dict[str, set[str]] = defaultdict(set)
    for path, path_kinds in kinds.items():
        nested = repo / path
        if "untracked" in path_kinds and nested.is_dir() and (nested / ".git").exists():
            for nested_path, nested_kinds in _raw_dirty_kinds(nested).items():
                prefixed = f"{path.rstrip('/')}/{nested_path}"
                expanded[prefixed].update(f"nested_{kind}" for kind in nested_kinds)
            continue
        expanded[path].update(path_kinds)
    return expanded


def _fingerprint(path: Path) -> dict[str, str]:
    if path.is_symlink():
        target = os.readlink(path)
        digest = hashlib.sha256(os.fsencode(target)).hexdigest()
        return {"type": "symlink", "sha256": digest}
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"type": "file", "sha256": digest}
    if path.is_dir():
        return {"type": "directory", "sha256": ""}
    return {"type": "absent", "sha256": ""}


def dirty_snapshot(repo: Path) -> dict[str, dict[str, object]]:
    repo = repo.resolve()
    return {
        path: {
            "kinds": sorted(kinds),
            "fingerprint": _fingerprint(repo / path),
        }
        for path, kinds in sorted(_dirty_kinds(repo).items())
    }


def _is_excluded(path: str, excluded: set[str]) -> bool:
    normalized = path.rstrip("/")
    return normalized in excluded or any(
        item.startswith(f"{normalized}/") for item in excluded
    )


def capture_baseline(
    repo: Path,
    output: Path,
    *,
    work_item_id: str,
    mode: str = "task_start",
    reason: str = "",
    exclude_paths: set[str] | None = None,
) -> bool:
    if output.exists():
        return False
    if mode not in {"task_start", "recovery"}:
        raise ValueError("unsupported baseline mode")
    reason = reason.strip()
    if mode == "recovery" and not reason:
        raise ValueError("recovery baseline requires a reason")

    excluded = {path.rstrip("/") for path in (exclude_paths or set()) if path.strip()}
    entries = {
        path: value
        for path, value in dirty_snapshot(repo).items()
        if not _is_excluded(path, excluded)
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "work_item_id": work_item_id,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": mode,
        "reason": reason,
        "repo_head": _head(repo),
        "entries": entries,
        "excluded_paths": sorted(excluded),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    return True


def changed_since_baseline(repo: Path, baseline: Path) -> list[str]:
    try:
        payload = json.loads(baseline.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"invalid worktree baseline: {baseline}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(
        payload.get("entries"), dict
    ):
        raise ValueError(f"invalid worktree baseline schema: {baseline}")

    before = {
        path: value
        for path, value in payload["entries"].items()
        if not _is_generated_cache(path)
    }
    after = dirty_snapshot(repo)
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture")
    capture.add_argument("--repo", required=True)
    capture.add_argument("--output", required=True)
    capture.add_argument("--work-item-id", required=True)
    capture.add_argument(
        "--mode", choices=("task_start", "recovery"), default="task_start"
    )
    capture.add_argument("--reason", default="")
    capture.add_argument("--exclude", action="append", default=[])

    changed = sub.add_parser("changed")
    changed.add_argument("--repo", required=True)
    changed.add_argument("--baseline", required=True)

    args = parser.parse_args()
    if args.command == "capture":
        created = capture_baseline(
            Path(args.repo),
            Path(args.output),
            work_item_id=args.work_item_id,
            mode=args.mode,
            reason=args.reason,
            exclude_paths=set(args.exclude),
        )
        print(
            json.dumps({"created": created, "output": args.output}, ensure_ascii=False)
        )
        return 0

    print(
        json.dumps(
            changed_since_baseline(Path(args.repo), Path(args.baseline)),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
