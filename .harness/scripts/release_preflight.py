#!/usr/bin/env python3
"""Open-source release preflight for harness-engineering."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from harness_output import dump_json


SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
LOCAL_STATE_FILES = {
    ".env",
    ".env.local",
    ".product-root",
    ".harness/products/registry.yaml",
    ".harness/products/active-product.json",
}
LOCAL_STATE_GLOBS = (
    ".harness/products/active-product*.json",
)
SECRET_FILE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".crt"}
LOCAL_PATH_RE = re.compile(r"(/Users/[^\\s`\"']+|/private/var/[^\\s`\"']+|[A-Za-z]:\\\\[^\\s`\"']+)")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(api[_-]?key|app[_-]?secret|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}"
)
CODE_REFERENCE_MARKERS = (
    "args.",
    "os.environ",
    "cfg.get",
    "data.get",
    "self.",
    "provider.",
    "_get_access_token",
    "access_token=access_token",
    "fake_token",
    "removeprefix",
    "LOCAL_PATH_RE",
    "SECRET_ASSIGNMENT_RE",
)


def emit(decision: str, reason: str, **extra: Any) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return True
    return b"\0" in chunk


def iter_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


def looks_like_static_secret(line: str) -> bool:
    if any(marker in line for marker in CODE_REFERENCE_MARKERS):
        return False
    return bool(SECRET_ASSIGNMENT_RE.search(line))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--max-issues", type=int, default=80)
    args = parser.parse_args()

    root = Path(args.harness_root).resolve()
    issues: list[str] = []

    for item in sorted(LOCAL_STATE_FILES):
        path = root / item
        if path.exists():
            issues.append(f"LOCAL_STATE_FILE:{item}")
    for pattern in LOCAL_STATE_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel_path = rel(path, root)
            if rel_path not in LOCAL_STATE_FILES and not rel_path.endswith(".example.json"):
                issues.append(f"LOCAL_STATE_FILE:{rel_path}")

    for path in iter_files(root):
        rel_path = rel(path, root)
        if path.suffix in SECRET_FILE_SUFFIXES:
            issues.append(f"SECRET_LIKE_FILE:{rel_path}")
            continue
        if is_binary(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "LOCAL_PATH_RE" not in line and LOCAL_PATH_RE.search(line):
                issues.append(f"LOCAL_ABSOLUTE_PATH:{rel_path}:{lineno}")
            if looks_like_static_secret(line):
                issues.append(f"SECRET_LIKE_ASSIGNMENT:{rel_path}:{lineno}")
            if len(issues) >= args.max_issues:
                break
        if len(issues) >= args.max_issues:
            break

    if issues:
        emit("block", f"RELEASE_PREFLIGHT_BLOCKED: {len(issues)} issue(s)", issues=issues[: args.max_issues])
        return 0

    emit("pass", "RELEASE_PREFLIGHT_OK: 未发现本机状态、绝对路径或疑似密钥")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
