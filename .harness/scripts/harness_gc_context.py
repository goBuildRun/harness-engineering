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
GIT_LOCAL_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
)


def _clean_git_env() -> dict[str, str]:
    environment = dict(os.environ)
    for name in GIT_LOCAL_ENV_VARS:
        environment.pop(name, None)
    return environment


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
    environment = _clean_git_env()
    completed = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary", base_ref, "--", *changed],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, errors="replace", check=False, env=environment,
    )
    patch = completed.stdout if completed.returncode == 0 else ""
    try:
        raw_untracked = subprocess.check_output(
            ["git", "ls-files", "-z", "--others", "--exclude-standard"],
            cwd=repo, stderr=subprocess.DEVNULL, env=environment,
        )
        untracked = {
            os.fsdecode(value) for value in raw_untracked.split(b"\0") if value
        }
    except (FileNotFoundError, subprocess.CalledProcessError):
        untracked = set()
    additions: list[str] = []
    for rel in changed:
        if rel not in untracked or not (repo / rel).is_file():
            continue
        completed = subprocess.run(
            ["git", "diff", "--no-index", "--no-ext-diff", "--binary", "--",
             "/dev/null", rel],
            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace", check=False, env=environment,
        )
        if completed.returncode not in {0, 1}:
            continue
        additions.append(completed.stdout)
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
