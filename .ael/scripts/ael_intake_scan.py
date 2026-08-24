#!/usr/bin/env python3
"""Collect bounded brownfield repository facts for intake reports."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ael_intake_policy import (
    CODE_EXTS,
    ENTRY_CANDIDATE_NAMES,
    ENTRY_KEYWORDS,
    ENTRY_LOW_PRIORITY_PARTS,
    MARKER_RE,
    MARKER_SKIP_NAMES,
    SECRET_VALUE_RE,
    LONG_TOKEN_RE,
    intake_policy,
    is_code_or_config,
    is_doc,
    iter_product_files,
    match_scope,
    rel,
    scope_label,
    scope_rows,
)
from workspace_paths import Phase0Layout

def read_text_limited(path: Path, limit: int = 200_000) -> str:
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    if b"\x00" in data:
        return ""
    return data.decode("utf-8", errors="ignore")


def headings(path: Path, max_items: int = 3) -> list[str]:
    text = read_text_limited(path, 120_000)
    found: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
        if match:
            found.append(match.group(2).strip()[:80])
        if len(found) >= max_items:
            break
    return found


def doc_score(path: Path) -> tuple[int, str]:
    parts = [p.lower() for p in path.parts]
    name = path.name.lower()
    if name == "readme.md":
        return (0, str(path))
    if "architecture" in parts or name in {"architecture.md", "adr.md"}:
        return (1, str(path))
    if "docs" in parts:
        return (2, str(path))
    if name in {"onboarding.md", "contributing.md"}:
        return (3, str(path))
    return (4, str(path))


def top_level_inventory(layout: Phase0Layout) -> dict[str, list[str]]:
    dirs: list[str] = []
    files: list[str] = []
    for path in sorted(layout.product_root.iterdir(), key=lambda p: p.name.lower()):
        if path.is_dir():
            if skip_dir(layout, path):
                continue
            dirs.append(path.name + "/")
        elif path.is_file() and not skip_file(path):
            files.append(path.name)
    return {"dirs": dirs[:40], "files": files[:40]}


def module_inventory(layout: Phase0Layout) -> list[dict[str, Any]]:
    roots = ("services", "apps", "packages", "modules", "libs", "src", "backend", "frontend")
    modules: list[dict[str, Any]] = []
    for name in roots:
        root = layout.product_root / name
        if not root.is_dir() or skip_dir(layout, root):
            continue
        children = [p.name + "/" for p in sorted(root.iterdir(), key=lambda p: p.name.lower()) if p.is_dir() and not skip_dir(layout, p)]
        modules.append({"root": rel(layout, root), "children": children[:30]})
    return modules


def package_json_signal(path: Path) -> dict[str, Any]:
    text = read_text_limited(path, 300_000)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"scripts": [], "frameworks": []}
    scripts = sorted((data.get("scripts") or {}).keys())
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    frameworks = [name for name in ("next", "react", "vue", "svelte", "vite", "express", "nestjs", "jest", "vitest", "playwright") if name in deps]
    return {"scripts": scripts[:20], "frameworks": frameworks}


def detect_stacks(layout: Phase0Layout, files: list[Path]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    names = {rel(layout, p): p for p in files}
    stack_by_name = {
        "package.json": "Node.js / JavaScript / TypeScript",
        "pyproject.toml": "Python",
        "requirements.txt": "Python",
        "pom.xml": "Java / Maven",
        "build.gradle": "Java/Kotlin / Gradle",
        "build.gradle.kts": "Java/Kotlin / Gradle",
        "go.mod": "Go",
        "Cargo.toml": "Rust",
        "composer.json": "PHP",
        "Gemfile": "Ruby",
        "Dockerfile": "Docker",
        "docker-compose.yml": "Docker Compose",
        "docker-compose.yaml": "Docker Compose",
        "Makefile": "Make",
        ".gitlab-ci.yml": "GitLab CI",
    }
    for path in files:
        name = path.name
        if name not in stack_by_name:
            continue
        item: dict[str, Any] = {"path": rel(layout, path), "signal": stack_by_name[name]}
        if name == "package.json":
            item.update(package_json_signal(path))
        signals.append(item)
    for key, path in sorted(names.items()):
        if key.startswith(".github/workflows/") and path.suffix.lower() in {".yml", ".yaml"}:
            signals.append({"path": key, "signal": "GitHub Actions"})
    return signals[:80]


def testing_signals(layout: Phase0Layout, files: list[Path]) -> list[str]:
    signals: list[str] = []
    for path in files:
        r = rel(layout, path)
        lower = r.lower()
        if "/tests/" in f"/{lower}/" or "/test/" in f"/{lower}/" or "__tests__" in lower:
            signals.append(r)
        elif path.name in {"pytest.ini", "tox.ini", "jest.config.js", "jest.config.ts", "vitest.config.ts", "vitest.config.js", "playwright.config.ts", "playwright.config.js"}:
            signals.append(r)
        elif lower.startswith(".github/workflows/") or path.name == ".gitlab-ci.yml":
            signals.append(r)
    return sorted(dict.fromkeys(signals))[:80]


def selected_docs(layout: Phase0Layout, docs: list[Path], policy: dict[str, Any], global_limit: int = 80, per_scope_limit: int = 24) -> list[Path]:
    """Select docs without letting one large upstream dependency crowd out the rest."""
    selected: list[Path] = []
    seen: set[Path] = set()

    def add(paths: list[Path]) -> None:
        for path in paths:
            if path in seen:
                continue
            selected.append(path)
            seen.add(path)

    add(docs[:global_limit])
    for scope in scope_rows(policy):
        scoped = [path for path in docs if match_scope(rel(layout, path), policy).path == scope.path]
        add(scoped[:per_scope_limit])
    return selected


def entry_candidate_reason(path: Path) -> str:
    name = path.name
    lower_name = name.lower()
    stem = path.stem.lower()
    parts = [p.lower() for p in path.parts]
    if name in ENTRY_CANDIDATE_NAMES or lower_name in ENTRY_CANDIDATE_NAMES:
        return "标准运行/配置/代码入口"
    if lower_name.endswith("_middleware.py") or lower_name.endswith("-middleware.ts"):
        return "middleware 入口"
    if any(keyword in stem for keyword in ENTRY_KEYWORDS) and path.suffix.lower() in CODE_EXTS:
        return "核心逻辑候选"
    if "routers" in parts and path.suffix.lower() in {".py", ".ts", ".tsx"}:
        return "API route/router 候选"
    return ""


def entry_score(layout: Phase0Layout, path: Path) -> tuple[int, int, str]:
    rel_path = rel(layout, path).lower()
    name = path.name.lower()
    parts = set(rel_path.split("/"))
    priority = {
        "langgraph.json": 0,
        "app.py": 1,
        "server.py": 1,
        "main.py": 1,
        "core.py": 2,
        "factory.py": 2,
        "agent.py": 2,
        "tools.py": 3,
        "middleware.py": 3,
        "retriever.py": 3,
        "service.py": 3,
        "router.py": 4,
        "routes.py": 4,
        "client.py": 5,
        "config.py": 5,
        "package.json": 6,
        "pyproject.toml": 6,
        "docker-compose.yml": 7,
        "docker-compose.yaml": 7,
        "makefile": 7,
    }.get(name, 8)
    if parts & ENTRY_LOW_PRIORITY_PARTS:
        priority += 20
    return (priority, len(rel_path), rel_path)


def selected_entrypoints(
    layout: Phase0Layout,
    files: list[Path],
    policy: dict[str, Any],
    global_limit: int = 100,
    per_scope_limit: int = 24,
) -> list[Path]:
    """Select likely code entry points per ownership scope for deep review."""
    candidates = sorted(
        [path for path in files if entry_candidate_reason(path)],
        key=lambda p: entry_score(layout, p),
    )
    selected: list[Path] = []
    seen: set[Path] = set()

    def add(paths: list[Path]) -> None:
        for path in paths:
            if path in seen:
                continue
            selected.append(path)
            seen.add(path)

    add(candidates[:global_limit])
    for scope in scope_rows(policy):
        scoped = [path for path in candidates if match_scope(rel(layout, path), policy).path == scope.path]
        add(scoped[:per_scope_limit])
    return selected


def redact(text: str) -> str:
    cleaned = SECRET_VALUE_RE.sub(r"\1=<redacted>", text)
    cleaned = LONG_TOKEN_RE.sub("<redacted-token>", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()[:220]


def marker_candidates(layout: Phase0Layout, files: list[Path], max_markers: int, policy: dict[str, Any]) -> tuple[list[dict[str, str]], int]:
    candidates: list[dict[str, str]] = []
    ignored = 0
    for path in files:
        if not (is_code_or_config(path) or is_doc(path)):
            continue
        if path.name in MARKER_SKIP_NAMES:
            continue
        text = read_text_limited(path)
        if not text:
            continue
        rel_path = rel(layout, path)
        scope = match_scope(rel_path, policy)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not MARKER_RE.search(line):
                continue
            if scope.debt == "ignore":
                ignored += 1
                continue
            if len(candidates) < max_markers:
                candidates.append(
                    {
                        "path": rel_path,
                        "scope": scope.label(),
                        "line": str(line_no),
                        "text": redact(line),
                    }
                )
    return candidates, ignored


def collect(layout: Phase0Layout, max_files: int, max_markers: int) -> dict[str, Any]:
    policy = intake_policy(layout)
    files = iter_product_files(layout, max_files)
    docs = sorted([p for p in files if is_doc(p)], key=doc_score)
    code_files = [p for p in files if is_code_or_config(p)]
    suffix_counts = Counter(p.suffix.lower() or p.name for p in code_files)
    doc_items = [
        {"path": rel(layout, path), "scope": scope_label(rel(layout, path), policy), "headings": headings(path)}
        for path in selected_docs(layout, docs, policy)
    ]
    entry_items = [
        {"path": rel(layout, path), "scope": scope_label(rel(layout, path), policy), "reason": entry_candidate_reason(path)}
        for path in selected_entrypoints(layout, files, policy)
    ]
    stacks = detect_stacks(layout, files)
    for item in stacks:
        item["scope"] = scope_label(str(item["path"]), policy)
    tests = [{"path": item, "scope": scope_label(item, policy)} for item in testing_signals(layout, files)]
    markers, ignored_markers = marker_candidates(layout, files, max_markers, policy)
    return {
        "file_count": len(files),
        "doc_count": len(docs),
        "code_count": len(code_files),
        "policy": policy,
        "top_level": top_level_inventory(layout),
        "modules": module_inventory(layout),
        "docs": doc_items,
        "entries": entry_items,
        "stacks": stacks,
        "tests": tests,
        "markers": markers,
        "ignored_markers": ignored_markers,
        "suffix_counts": suffix_counts.most_common(20),
        "truncated": len(files) >= max_files,
    }
