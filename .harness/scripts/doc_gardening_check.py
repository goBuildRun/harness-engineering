#!/usr/bin/env python3
"""doc_gardening_check.py — Markdown 链接健康 + product workspace 陈旧路径扫描。"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

from doc_gardening_policy import (
    ALLOW_DOCS_PRODUCT_SPECS_FILES,
    ALLOW_LINE_SUBSTRINGS,
    ARCHITECTURE_REQUIRED_TERMS,
    BARE_AGENT_WS,
    ENTRY_DOC_LIMITS,
    EXPLICIT_ANCHOR_RE,
    HARNESS_TASKS_OUTPUT,
    HEADING_RE,
    LINK_RE,
    STALE_PATTERN_RULES,
    STALE_SKIP_REL_PATHS,
)
from harness_output import dump_json

def emit(decision: str, reason: str) -> None:
    dump_json({"decision": decision, "reason": reason})


def rel_to_harness(path: Path, harness_root: Path) -> str:
    try:
        return str(path.relative_to(harness_root)).replace("\\", "/")
    except ValueError:
        return path.name


def strip_fenced_code(text: str) -> str:
    """移除 ``` 代码块内容，避免目录树/反模式表误报。"""
    return re.sub(r"```[\s\S]*?```", "", text)


def resolve_link_target(source: Path, product_root: Path, target: str) -> Path | None:
    clean = target.split("#")[0].strip()
    if not clean:
        return source
    if clean.startswith(("http://", "https://", "mailto:")):
        return None
    if not (clean.startswith("./") or clean.startswith("../")):
        return None

    resolved = (source.parent / clean).resolve()
    if resolved.is_file() or resolved.is_dir():
        return resolved

    # 从产品根再试（跨 harness-engineering / product workspace 边界）
    alt = (product_root / clean.lstrip("./")).resolve()
    if alt.is_file() or alt.is_dir():
        return alt

    # harness-workspace/...、phase0/... 或 harness-engineering/... 显式路径
    if clean.startswith("harness-workspace/") or clean.startswith("phase0/") or clean.startswith("harness-engineering/"):
        alt2 = (product_root / clean).resolve()
        if alt2.is_file() or alt2.is_dir():
            return alt2
        alt3 = (product_root.parent / clean).resolve()
        if alt3.is_file() or alt3.is_dir():
            return alt3
    return None


def link_target_exists(source: Path, product_root: Path, target: str) -> bool:
    clean = target.split("#")[0].strip()
    if clean.startswith(("http://", "https://", "mailto:")):
        return True
    if clean and not (clean.startswith("./") or clean.startswith("../")):
        return True
    return resolve_link_target(source, product_root, target) is not None


def github_anchor(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = "".join(ch for ch in text if ch.isalnum() or ch in "_- \t")
    return re.sub(r"\s", "-", text)


def markdown_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            base = github_anchor(heading.group(1))
            count = counts.get(base, 0)
            counts[base] = count + 1
            anchors.add(base if count == 0 else f"{base}-{count}")
        anchors.update(EXPLICIT_ANCHOR_RE.findall(line))
    return anchors


def line_allowed(rel: str, line: str, pattern_label: str) -> bool:
    if any(s in line for s in ALLOW_LINE_SUBSTRINGS):
        return True
    if pattern_label.startswith("STALE_PHASE0") and rel in ALLOW_DOCS_PRODUCT_SPECS_FILES:
        return True
    if "phase0.agent_workspace" in line:
        return True
    return False


def active_profile(product_root: Path) -> str:
    for rel in (
        "harness-workspace/project.yaml",
        "harness-workspace/config.yaml",
        ".harness-engineering.yaml",
    ):
        path = product_root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"(?m)^\s*profile:\s*['\"]?([A-Za-z0-9_-]+)['\"]?\s*$", text)
        if match:
            return match.group(1)
    return ""


def include_reference_doc(path: Path, harness_root: Path, profile: str) -> bool:
    try:
        rel = path.relative_to(harness_root)
    except ValueError:
        return True
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "docs" and parts[1] == "references":
        ref_profile = parts[2]
        if ref_profile != "index.md" and path.is_relative_to(harness_root / "docs" / "references" / ref_profile):
            return not profile or ref_profile == profile
    return True


def collect_markdown_files(harness_root: Path, product_root: Path) -> list[Path]:
    files: list[Path] = []
    profile = active_profile(product_root)
    files.extend(p for p in harness_root.glob("docs/**/*.md") if include_reference_doc(p, harness_root, profile))
    for name in ("README.md", "ARCHITECTURE.md", "AGENTS.md", "CLAUDE.md"):
        p = harness_root / name
        if p.is_file():
            files.append(p)
    files.extend((harness_root / ".harness").glob("**/*.md"))
    files.extend(harness_root.glob("tasks/**/*.md"))

    for sub in ("harness-workspace", "architecture"):
        root = product_root / sub
        if root.is_dir():
            files.extend(root.glob("**/*.md"))

    for extra in (
        product_root / "README.md",
        product_root / "ONBOARDING.md",
        product_root / ".cursor/rules/harness-engineering.mdc",
    ):
        if extra.is_file():
            files.append(extra)

    seen: set[Path] = set()
    unique: list[Path] = []
    for p in files:
        rp = p.resolve()
        if rp not in seen and rp.is_file():
            seen.add(rp)
            unique.append(rp)
    return unique


def check_links(harness_root: Path, product_root: Path, md_files: list[Path]) -> list[str]:
    issues: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}
    for md in md_files:
        rel_s = rel_to_harness(md, harness_root)
        text = md.read_text(encoding="utf-8", errors="ignore")
        for m in LINK_RE.finditer(text):
            target = m.group(1).strip()
            if not link_target_exists(md, product_root, target):
                issues.append(f"BROKEN_LINK:{rel_s}->{target}")
                continue

            if "#" not in target or target.startswith(("http://", "https://", "mailto:")):
                continue
            _path_part, fragment = target.split("#", 1)
            fragment = urllib.parse.unquote(fragment).lower()
            resolved = resolve_link_target(md, product_root, target)
            if not fragment or resolved is None or resolved.suffix.lower() not in {".md", ".mdc"}:
                continue
            anchors = anchor_cache.setdefault(resolved, markdown_anchors(resolved))
            if fragment not in anchors:
                issues.append(f"BROKEN_ANCHOR:{rel_s}->{target}")
    return issues


def check_stale_patterns(harness_root: Path, md_files: list[Path]) -> list[str]:
    issues: list[str] = []
    for md in md_files:
        rel_s = rel_to_harness(md, harness_root)
        if rel_s in STALE_SKIP_REL_PATHS:
            continue

        prose = strip_fenced_code(md.read_text(encoding="utf-8", errors="ignore"))
        for i, line in enumerate(prose.splitlines(), start=1):
            if line.strip().startswith("|") and "模式" in line or (
                line.strip().startswith("| `") and rel_s == "docs/HARNESS_DOC_CONSISTENCY.md"
            ):
                continue

            for pattern, label in STALE_PATTERN_RULES:
                if pattern.search(line) and not line_allowed(rel_s, line, label):
                    issues.append(f"{label} @ {rel_s}:{i}")

            if HARNESS_TASKS_OUTPUT.search(line) and not line_allowed(rel_s, line, "STALE_TASKS"):
                issues.append(f"STALE_TASKS: 任务产出应在 harness-workspace/planning/tasks/ @ {rel_s}:{i}")

            if BARE_AGENT_WS.search(line) and not line_allowed(rel_s, line, "STALE_WS"):
                issues.append(f"STALE_WS: 工作区应在 harness-workspace/runs/ @ {rel_s}:{i}")

    return issues


def check_legacy_tool_ref(harness_root: Path) -> list[str]:
    issues: list[str] = []
    for p in harness_root.rglob("*.md"):
        try:
            if "tools/check.sh" in p.read_text(encoding="utf-8"):
                issues.append(f"STALE_REF:{p.relative_to(harness_root)}")
        except OSError:
            pass
    return issues


def check_entropy_files(harness_root: Path, product_root: Path) -> list[str]:
    issues: list[str] = []
    for root in (harness_root,):
        if not root.exists():
            continue
        for p in root.rglob(".DS_Store"):
            issues.append(f"ENTROPY_FILE:{p}")
    return issues


def check_entry_doc_shape(harness_root: Path) -> list[str]:
    """Guard OpenAI Harness Engineering information architecture invariants."""
    issues: list[str] = []
    for rel, limits in ENTRY_DOC_LIMITS.items():
        path = harness_root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        if "max_lines" in limits and len(lines) > int(limits["max_lines"]):
            issues.append(f"ENTRY_DOC_TOO_LONG:{rel}:{len(lines)}>{limits['max_lines']}")
        if "min_lines" in limits and len(lines) < int(limits["min_lines"]):
            issues.append(f"ENTRY_DOC_TOO_SHORT:{rel}:{len(lines)}<{limits['min_lines']}")
        ref_count = text.count(".harness")
        if ref_count > int(limits.get("max_harness_refs", 999)):
            issues.append(f"ENTRY_DOC_INTERNALS_OVEREXPOSED:{rel}:{ref_count}>{limits['max_harness_refs']}")

    agents = harness_root / "AGENTS.md"
    if agents.is_file():
        text = agents.read_text(encoding="utf-8", errors="ignore")
        if "```" in text:
            issues.append("AGENTS_NOT_A_MAP: fenced code block found")
        for noisy in ("Phase 0 必会命令", "Phase 1 必会命令", "完整逐步说明"):
            if noisy in text:
                issues.append(f"AGENTS_NOT_A_MAP: contains '{noisy}'")

    architecture = harness_root / "ARCHITECTURE.md"
    if architecture.is_file():
        text = architecture.read_text(encoding="utf-8", errors="ignore")
        for term in ARCHITECTURE_REQUIRED_TERMS:
            if term not in text:
                issues.append(f"ARCHITECTURE_INCOMPLETE: missing {term}")

    claude = harness_root / "CLAUDE.md"
    if claude.is_file():
        text = claude.read_text(encoding="utf-8", errors="ignore")
        if "RUNTIME MARKER" in text:
            issues.append("CLAUDE_RUNTIME_STATE: remove runtime marker from repo instruction")
        if "两方" in text:
            issues.append("CLAUDE_STALE_QA_GATE: use qa_sign_off + subagent gate + QA evidence")
        if "QA evidence check" not in text and "qa_evidence_check" not in text:
            issues.append("CLAUDE_QA_EVIDENCE_MISSING")
    return issues


def check_location_independent_docs(harness_root: Path, product_root: Path, md_files: list[Path]) -> list[str]:
    issues: list[str] = []
    for md in md_files:
        rel_s = rel_to_harness(md, harness_root)
        text = md.read_text(encoding="utf-8", errors="ignore")
        if md.is_relative_to(harness_root):
            if "../harness-workspace" in text:
                issues.append(f"LOCATION_COUPLED_DOC:{rel_s}: use $PRODUCT_ROOT/harness-workspace")
            if "cd ../../harness-engineering" in text:
                issues.append(f"LOCATION_COUPLED_DOC:{rel_s}: harness-engineering can be anywhere")
        elif md.is_relative_to(product_root / "harness-workspace"):
            if "runtime 当前位于" in text or "cd ../../harness-engineering" in text:
                issues.append(f"PRODUCT_WORKSPACE_COUPLED:{rel_s}: do not pin harness-engineering path")
    return issues


def main() -> int:
    if len(sys.argv) < 2:
        emit("block", "USAGE: doc_gardening_check.py <harness_root> [product_root]")
        return 0

    harness_root = Path(sys.argv[1]).resolve()
    product_root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else harness_root.parent

    md_files = collect_markdown_files(harness_root, product_root)
    issues: list[str] = []
    issues.extend(check_links(harness_root, product_root, md_files))
    issues.extend(check_stale_patterns(harness_root, md_files))
    issues.extend(check_legacy_tool_ref(harness_root))
    issues.extend(check_entropy_files(harness_root, product_root))
    issues.extend(check_entry_doc_shape(harness_root))
    issues.extend(check_location_independent_docs(harness_root, product_root, md_files))

    if issues:
        preview = "; ".join(issues[:20])
        extra = f" (+{len(issues) - 20} more)" if len(issues) > 20 else ""
        emit("block", f"DOC_GARDENING: {preview}{extra}")
    else:
        emit(
            "pass",
            f"DOC_GARDENING: {len(md_files)} 个 markdown 无断链且无 product workspace 陈旧路径",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
