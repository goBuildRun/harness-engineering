#!/usr/bin/env python3
"""Read immutable candidate entries and path sets from Git commits."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def resolve_commit(repo: Path, commit: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"], cwd=repo,
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def commit_paths(repo: Path, baseline: str, commit: str) -> list[str] | None:
    try:
        completed = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", "-z", baseline, commit],
            cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return sorted(os.fsdecode(item) for item in completed.stdout.split(b"\0") if item)


def commit_entry(repo: Path, commit: str, rel: str) -> dict[str, str]:
    try:
        output = subprocess.check_output(
            ["git", "ls-tree", "-z", commit, "--", rel], cwd=repo,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        output = b""
    records = [record for record in output.split(b"\0") if record]
    if len(records) != 1:
        return {"path": rel, "kind": "absent", "mode": "000000", "sha256": "absent"}
    try:
        metadata, encoded_path = records[0].split(b"\t", 1)
        mode_bytes, object_type, object_id = metadata.split(b" ", 2)
        tree_path = os.fsdecode(encoded_path)
    except ValueError:
        return {"path": rel, "kind": "invalid", "mode": "invalid", "sha256": "invalid"}
    mode = mode_bytes.decode("ascii", errors="replace")
    kind = "symlink" if mode == "120000" and object_type == b"blob" else (
        "file" if mode in {"100644", "100755"} and object_type == b"blob" else "invalid"
    )
    if tree_path != rel or kind == "invalid":
        return {"path": rel, "kind": kind, "mode": mode, "sha256": "invalid"}
    try:
        content = subprocess.check_output(
            ["git", "cat-file", "blob", object_id.decode("ascii")], cwd=repo,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {"path": rel, "kind": kind, "mode": mode, "sha256": "invalid"}
    return {
        "path": rel, "kind": kind, "mode": mode,
        "sha256": hashlib.sha256(content).hexdigest(),
    }
