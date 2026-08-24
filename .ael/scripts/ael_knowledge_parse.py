#!/usr/bin/env python3
"""Pure Markdown and BMAD artifact parsing for product knowledge sync."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from workspace_paths import Phase0Layout

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

SKIP_PLANNING_NAMES = {"index.md", "_template.md", "README.md"}


def read_text(path: Path, limit: int = 300_000) -> str:
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    if b"\x00" in data:
        return ""
    return data.decode("utf-8", errors="ignore")


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    raw = parts[1].strip()
    body = parts[2].lstrip("\n")
    if yaml is None:
        return {}, body
    try:
        meta = yaml.safe_load(raw) or {}
    except Exception:
        meta = {}
    return meta if isinstance(meta, dict) else {}, body


def first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return compact(match.group(1), 80)
    return fallback


def compact(text: Any, limit: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def md_cell(text: Any) -> str:
    return compact(text, 220).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def section_text(text: str, names: tuple[str, ...]) -> str:
    needles = tuple(name.lower() for name in names)
    pattern = re.compile(r"(?ms)^##+\s+(.+?)\s*$\n(.*?)(?=^##+\s+|\Z)")
    for match in pattern.finditer(text):
        heading = match.group(1).strip().lower()
        if any(needle in heading for needle in needles):
            return match.group(2).strip()
    return ""


def section_summary(text: str, names: tuple[str, ...], limit: int = 180) -> str:
    section = section_text(text, names)
    if not section:
        return ""
    lines = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|") or set(stripped) <= {"-", " "}:
            continue
        lines.append(stripped.lstrip("-* ").strip())
        if len(" ".join(lines)) > limit:
            break
    return compact("；".join(lines), limit)


def section_bullets(text: str, names: tuple[str, ...], limit: int = 3) -> list[str]:
    section = section_text(text, names)
    bullets: list[str] = []
    for line in section.splitlines():
        match = re.match(r"^\s*-\s+\[[ xX]\]\s*(.+)$", line)
        if not match:
            match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if match:
            bullets.append(compact(match.group(1), 90))
        if len(bullets) >= limit:
            break
    return bullets


def meta_list(meta: dict[str, Any], key: str) -> list[str]:
    raw = meta.get(key) or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def field_value(text: str, label: str) -> str:
    pattern = re.compile(rf"^\s*-\s*{re.escape(label)}[：:]\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    return compact(match.group(1), 120) if match else ""


def markdown_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        p
        for p in root.glob("*.md")
        if p.is_file() and p.name not in SKIP_PLANNING_NAMES and not p.name.startswith(".")
    )


def raw_bmad_artifacts(layout: Phase0Layout) -> list[Path]:
    roots = (
        layout.bmad_output_root / "planning-artifacts",
        layout.bmad_output_root / "design-artifacts",
    )
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(sorted(p for p in root.rglob("*.md") if p.is_file()))
    return sorted(files)[:60]


def artifact_kind(path: Path, meta: dict[str, Any], title: str) -> str:
    explicit = meta.get("artifact_type") or meta.get("type") or meta.get("kind")
    if explicit:
        return compact(explicit, 40)
    haystack = f"{path.name} {title}".lower()
    if any(token in haystack for token in ("prd", "brief", "requirement", "spec", "产品", "需求", "蓝图")):
        return "产品/需求"
    if any(token in haystack for token in ("architecture", "design", "solution", "架构", "方案")):
        return "架构/方案"
    if any(token in haystack for token in ("research", "analysis", "调研", "分析")):
        return "分析/调研"
    if any(token in haystack for token in ("readiness", "check", "就绪", "验收")):
        return "就绪/验收"
    return "BMAD planning"


def artifact_summary(body: str, title: str) -> str:
    summary = section_summary(
        body,
        (
            "目标",
            "产品目标",
            "项目目标",
            "产品蓝图",
            "背景",
            "需求背景",
            "架构",
            "Architecture",
            "方案",
            "Solution",
            "技术方案",
            "就绪检查结论",
            "验收标准",
            "Acceptance Criteria",
            "风险",
        ),
        220,
    )
    if summary:
        return summary
    bullets = section_bullets(body, ("摘要", "Summary", "Overview", "概览"), 2)
    return "；".join(bullets) or title
