#!/usr/bin/env python3
"""Build bounded, task-scoped context for an independent GC runner."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rs"}
IMPORT_RE = re.compile(
    r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+)|.*from\s+['\"]([^'\"]+)['\"])",
    re.MULTILINE,
)


def direct_dependencies(repo: Path, changed: list[str]) -> list[str]:
    dependencies: set[str] = set()
    for rel in changed:
        path = repo / rel
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in IMPORT_RE.finditer(text):
            imported = next((value for value in match.groups() if value), "").lstrip("./")
            stem = imported.replace(".", "/")
            candidates = [stem + suffix for suffix in CODE_SUFFIXES] + [stem + "/__init__.py"]
            dependencies.update(
                candidate for candidate in candidates
                if candidate not in changed and (repo / candidate).is_file()
            )
    return sorted(dependencies)


def actual_diff(repo: Path, changed: list[str], base_ref: str = "HEAD") -> str:
    if not changed:
        return ""
    completed = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary", base_ref, "--", *changed],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, errors="replace", check=False,
    )
    patch = completed.stdout if completed.returncode == 0 else ""
    try:
        untracked = set(subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo, text=True, stderr=subprocess.DEVNULL,
        ).splitlines())
    except (FileNotFoundError, subprocess.CalledProcessError):
        untracked = set()
    additions: list[str] = []
    for rel in changed:
        path = repo / rel
        if rel not in untracked or not path.is_file():
            continue
        raw = path.read_bytes()
        body = raw.decode("utf-8", errors="replace") if b"\0" not in raw else f"<binary {len(raw)} bytes>"
        additions.append(f"diff --git a/{rel} b/{rel}\nnew file\n--- /dev/null\n+++ b/{rel}\n{body}")
    return patch + ("\n" if patch and additions else "") + "\n".join(additions)


def build_gc_context(result: dict[str, Any], mechanical: dict[str, Any],
                     changed: list[str], repo: Path, base_ref: str = "HEAD") -> tuple[dict[str, Any], int]:
    context = {
        "task_id": result["task_id"],
        "task_contract": result.get("task") or {"task_id": result["task_id"]},
        "scope": changed,
        "actual_diff": actual_diff(repo, changed, base_ref), "changed_files": changed,
        "triggers": mechanical.get("triggers", []),
        "direct_dependencies": direct_dependencies(repo, changed),
        "constraints": ["no_new_features", "no_acceptance_change", "no_scope_expansion"],
        "result_contract": {"role": "gc-sweeper", "independent": True,
                            "task_id": result["task_id"],
                            "policy_digest": result.get("policy_digest", "")},
    }
    context_chars = len(json.dumps(context, ensure_ascii=False))
    limit = int(os.environ.get("HARNESS_GC_CONTEXT_MAX_CHARS") or "200000")
    if context_chars > limit:
        raise ValueError(f"GC_CONTEXT_BUDGET_EXCEEDED: {context_chars}>{limit}")
    return context, context_chars
