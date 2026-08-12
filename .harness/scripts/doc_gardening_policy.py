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
        "docs/BMAD_Prelude.md",
        "docs/HARNESS_DOC_CONSISTENCY.md",
        "docs/product-specs/README.md",
        ".harness/scripts/phase0_paths.py",
        ".harness/rules/doc-boundary.md",
    }
)

# 整文件跳过「陈旧路径」扫描（元文档 / 兼容说明）
STALE_SKIP_REL_PATHS = frozenset(
    {
        "docs/HARNESS_DOC_CONSISTENCY.md",
        "docs/BMAD_Prelude.md",
        "docs/product-specs/README.md",
    }
)

STALE_PATTERN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"docs/product-specs/"), "STALE_PLANNING: 使用 harness-workspace/planning/product-specs/"),
    (re.compile(r"harness-workspace/phase0/"), "STALE_WORKSPACE: 使用 harness-workspace/planning/ 或 harness-workspace/runs/"),
    (re.compile(r"(?<![\w/-])phase0/product-specs/"), "STALE_PHASE0_PATH: 使用 harness-workspace/planning/product-specs/"),
    (re.compile(r"(?<![\w/-])phase0/exec-plans/"), "STALE_PHASE0_PATH: 使用 harness-workspace/planning/exec-plans/"),
    (re.compile(r"(?<![\w/-])phase0/tasks/"), "STALE_PHASE0_PATH: 使用 harness-workspace/planning/tasks/"),
    (re.compile(r"(?<![\w/-])phase0/\.agent-workspace/"), "STALE_RUNS: 使用 harness-workspace/runs/"),
    (re.compile(r"(?<![\w/-])harness-engineering-workflow"), "STALE_DIR: 目录已更名为 harness-engineering/"),
    (
        re.compile(r'TASK_DIR\s*=\s*["\']tasks/'),
        "STALE_TASK_DIR: 任务产出应在 harness-workspace/planning/tasks/",
    ),
    (
        re.compile(r"sync-spec\s+docs/product-specs/"),
        "STALE_SYNC: sync-spec 应指向 harness-workspace/planning/product-specs/",
    ),
    (
        re.compile(r"cp\s+docs/product-specs/"),
        "STALE_CP: 使用 .harness/templates/product-spec.md → harness-workspace/planning/product-specs/",
    ),
]

HARNESS_TASKS_OUTPUT = re.compile(r"(?<![_/])harness-engineering/tasks/(?!_templates)[^\s`\)]")

BARE_AGENT_WS = re.compile(r"(?<![\w])\.agent-workspace/")

LINK_RE = re.compile(r"\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
EXPLICIT_ANCHOR_RE = re.compile(r"<(?:a|span)\s+(?:name|id)=[\"']([^\"']+)[\"']", re.IGNORECASE)

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
