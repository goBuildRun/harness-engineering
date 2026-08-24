#!/usr/bin/env python3
"""Ownership, scope, and safe file-selection policy for brownfield intake."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workspace_paths import Phase0Layout

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

DOC_EXTS = {".md", ".mdx", ".rst", ".adoc", ".txt"}
CODE_EXTS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".sql",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".bash",
    ".zsh",
    ".dockerfile",
}
CONFIG_NAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "poetry.lock",
    "uv.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "Cargo.toml",
    "composer.json",
    "Gemfile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
    "Taskfile.yml",
    ".gitlab-ci.yml",
}
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".agent",
    ".agents",
    ".claude",
    ".codex",
    ".cursor",
    ".idea",
    ".trae",
    ".vscode",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    "htmlcov",
    "_bmad",
    "_bmad-output",
    "bmad-output",
}
SECRET_FILE_NAMES = {".DS_Store", ".env", ".env.local", ".env.development", ".env.production", ".env.test"}
SECRET_FILE_KEYWORDS = {
    "credential",
    "credentials",
    "service-account",
    "service_account",
    "sa-key",
    "private-key",
    "private_key",
}
MARKER_SKIP_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "Cargo.lock", "uv.lock"}
MARKER_RE = re.compile(
    r"(\b(?:TODO|FIXME|HACK|XXX|xfail|flaky|failing)\b|技术债|待修复|临时方案|临时|绕过)",
)
SECRET_VALUE_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|"
    r"APP[_-]?SECRET|PRIVATE[_-]?KEY)[A-Z0-9_]*)\s*[:=]\s*[^,\s]+"
)
LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_/\-+=]{32,}\b")
PRODUCT_CONFIG_CANDIDATES = (
    "ael-workspace/project.yaml",
    "ael-workspace/config.yaml",
    ".buildrun-agent-engineering-lifecycle.yaml",
)
ENTRY_CANDIDATE_NAMES = {
    "app.py",
    "server.py",
    "main.py",
    "core.py",
    "factory.py",
    "agent.py",
    "tools.py",
    "client.py",
    "config.py",
    "settings.py",
    "middleware.py",
    "router.py",
    "routes.py",
    "service.py",
    "retriever.py",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
    "langgraph.json",
}
ENTRY_KEYWORDS = (
    "agent",
    "middleware",
    "service",
    "server",
    "factory",
    "gateway",
    "router",
    "retriever",
    "workflow",
    "orchestrator",
    "context",
    "memory",
    "mcp",
)
ENTRY_LOW_PRIORITY_PARTS = {
    "tests",
    "test",
    "__tests__",
    "benchmark",
    "benchmarks",
    "examples",
    "example",
    "third_party",
}


@dataclass(frozen=True)
class IntakeScope:
    path: str
    role: str
    review: str
    debt: str
    description: str

    def label(self) -> str:
        return f"{self.role}; review={self.review}; debt={self.debt}"


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def rel(layout: Phase0Layout, path: Path) -> str:
    return layout.rel(path)


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None or not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}


def product_config(layout: Phase0Layout) -> dict[str, Any]:
    for candidate in PRODUCT_CONFIG_CANDIDATES:
        data = load_yaml(layout.product_root / candidate)
        if data:
            return data
    return {}


def clean_scope_path(raw: Any) -> str:
    path = str(raw or ".").strip().replace("\\", "/").strip("/")
    return path or "."


def normalize_scope(raw: dict[str, Any], fallback: IntakeScope) -> IntakeScope:
    return IntakeScope(
        path=clean_scope_path(raw.get("path") or fallback.path),
        role=str(raw.get("role") or fallback.role),
        review=str(raw.get("review") or fallback.review),
        debt=str(raw.get("debt") or fallback.debt),
        description=str(raw.get("description") or fallback.description),
    )


def intake_policy(layout: Phase0Layout) -> dict[str, Any]:
    cfg = product_config(layout)
    intake = cfg.get("intake") or {}
    default = normalize_scope(
        intake.get("default_scope") or {},
        IntakeScope(
            path=".",
            role="product-owned",
            review="full",
            debt="track",
            description="未匹配任何显式 scope 时，按产品自有内容处理。",
        ),
    )
    scopes = [
        normalize_scope(item, default)
        for item in intake.get("scopes") or []
        if isinstance(item, dict) and item.get("path")
    ]
    scopes = sorted(scopes, key=lambda s: (0 if s.path == "." else len(s.path)), reverse=True)
    return {"default": default, "scopes": scopes}


def match_scope(rel_path: str, policy: dict[str, Any]) -> IntakeScope:
    rel_path = rel_path.strip().replace("\\", "/").strip("/")
    for scope in policy["scopes"]:
        path = scope.path
        if path == ".":
            return scope
        if rel_path == path or rel_path.startswith(path + "/"):
            return scope
    return policy["default"]


def scope_label(rel_path: str, policy: dict[str, Any]) -> str:
    return match_scope(rel_path, policy).label()


def scope_rows(policy: dict[str, Any]) -> list[IntakeScope]:
    return [*policy["scopes"], policy["default"]]


def skip_dir(layout: Phase0Layout, path: Path) -> bool:
    if path == layout.product_root:
        return False
    if path.name.startswith(layout.workspace_root.name):
        return True
    if path.name in SKIP_DIRS:
        return True
    return is_under(path, layout.workspace_root)


def skip_file(path: Path) -> bool:
    name = path.name
    lower_name = name.lower()
    if name in SECRET_FILE_NAMES:
        return True
    if name.startswith(".env."):
        return True
    if path.suffix.lower() in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        if any(keyword in lower_name for keyword in SECRET_FILE_KEYWORDS):
            return True
    if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".crt", ".cer"}:
        return True
    return False


def iter_product_files(layout: Phase0Layout, max_files: int) -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(layout.product_root):
        root_path = Path(root)
        dirs[:] = sorted(d for d in dirs if not skip_dir(layout, root_path / d))
        for name in sorted(names):
            path = root_path / name
            if skip_file(path):
                continue
            files.append(path)
            if len(files) >= max_files:
                return files
    return files


def is_doc(path: Path) -> bool:
    if path.name in CONFIG_NAMES:
        return False
    if path.suffix.lower() in DOC_EXTS:
        return True
    return path.name.upper() in {"README", "LICENSE", "CHANGELOG"}


def is_code_or_config(path: Path) -> bool:
    return path.suffix.lower() in CODE_EXTS or path.name in CONFIG_NAMES
