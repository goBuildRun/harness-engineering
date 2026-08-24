#!/usr/bin/env python3
"""Authoritative documentation gardening policy constants."""
from __future__ import annotations

import re

ALLOW_LINE_SUBSTRINGS = (
    "→ phase0",
    "→ planning",
    "**非**",
    "非 `docs/product-specs",
    "非 docs/product-specs",
    "废弃",
    "不再",
    "legacy",
    "LEGACY",
    "旧写法",
    "旧路径",
    "兼容",
    "已清除",
    "历史 ",
    "勿在",
    "禁止",
    "不应",
    "不要",
    "勿用",
    "勿新建",
    "自动映射",
    "向后兼容",
    "STALE_PHASE0",
    "doc_gardening",
    "陈旧路径模式",
    "反模式",
    "历史兼容",
    "legacy path",
)

ALLOW_DOCS_PRODUCT_SPECS_FILES = frozenset(
    {
        "docs/planning/bmad-planning.md",
        "docs/governance/documentation.md",
        ".ael/scripts/phase0_paths.py",
        ".ael/rules/doc-boundary.md",
    }
)

# 整文件跳过「陈旧路径」扫描（元文档 / 兼容说明）
STALE_SKIP_REL_PATHS = frozenset(
    {
        "docs/governance/documentation.md",
        "docs/planning/bmad-planning.md",
    }
)

STALE_PATTERN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"docs/product-specs/"), "STALE_PLANNING: 使用 ael-workspace/planning/product-specs/"),
    (re.compile(r"ael-workspace/phase0/"), "STALE_WORKSPACE: 使用 ael-workspace/planning/ 或 ael-workspace/runs/"),
    (re.compile(r"(?<![\w/-])phase0/product-specs/"), "STALE_PHASE0_PATH: 使用 ael-workspace/planning/product-specs/"),
    (re.compile(r"(?<![\w/-])phase0/exec-plans/"), "STALE_PHASE0_PATH: 使用 ael-workspace/planning/exec-plans/"),
    (re.compile(r"(?<![\w/-])phase0/tasks/"), "STALE_PHASE0_PATH: 使用 ael-workspace/planning/tasks/"),
    (re.compile(r"(?<![\w/-])phase0/\.agent-workspace/"), "STALE_RUNS: 使用 ael-workspace/runs/"),
    (re.compile(r"(?<![\w/-])buildrun-agent-engineering-lifecycle-workflow"), "STALE_DIR: 目录已更名为 buildrun-agent-engineering-lifecycle/"),
    (
        re.compile(r'TASK_DIR\s*=\s*["\']tasks/'),
        "STALE_TASK_DIR: 任务产出应在 ael-workspace/planning/tasks/",
    ),
    (
        re.compile(r"sync-spec\s+docs/product-specs/"),
        "STALE_SYNC: sync-spec 应指向 ael-workspace/planning/product-specs/",
    ),
    (
        re.compile(r"cp\s+docs/product-specs/"),
        "STALE_CP: 使用 .ael/templates/product-spec.md → ael-workspace/planning/product-specs/",
    ),
]

AEL_TASKS_OUTPUT = re.compile(r"(?<![_/])buildrun-agent-engineering-lifecycle/tasks/(?!_templates)[^\s`\)]")

BARE_AGENT_WS = re.compile(r"(?<![\w])\.agent-workspace/")

LINK_RE = re.compile(r"\]\(([^)]+)\)")
LINK_LABEL_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
EXPLICIT_ANCHOR_RE = re.compile(r"<(?:a|span)\s+(?:name|id)=[\"']([^\"']+)[\"']", re.IGNORECASE)

LEGACY_DOC_LABELS = (
    "docs/USAGE.md",
    "docs/BMAD_Prelude.md",
    "docs/COLLABORATION.md",
    "docs/Brownfield_Intake.md",
    "docs/QUALITY.md",
    "docs/SECURITY.md",
    "docs/RELIABILITY.md",
    "docs/references/index.md",
)

ENTRY_DOC_LIMITS = {
    "AGENTS.md": {"max_lines": 85, "max_harness_refs": 8},
    "CLAUDE.md": {"max_lines": 90, "max_harness_refs": 8},
    "ARCHITECTURE.md": {"min_lines": 80, "max_harness_refs": 12},
    "README.md": {"max_harness_refs": 35},
}

ARCHITECTURE_REQUIRED_TERMS = (
    "System Boundary",
    "Product Workspace",
    "State And Truth Sources",
    "Gate Chain",
    "Progressive Disclosure",
)
