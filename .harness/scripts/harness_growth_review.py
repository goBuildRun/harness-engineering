#!/usr/bin/env python3
"""Review and apply Harness growth candidates to product knowledge."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from harness_knowledge import template_path
from workspace_paths import Phase0Layout

PENDING_RE = re.compile(r"人工决定\*\*[：:]\s*待定|处理结果\*\*[：:]\s*待处理")


def md_escape(text: object) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def latest_report(layout: Phase0Layout) -> Path | None:
    reports = sorted(layout.growth_reports_dir.glob("*-GROWTH.md")) if layout.growth_reports_dir.is_dir() else []
    return reports[-1] if reports else None


def legacy_marker_key(report_rel: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", report_rel)


def marker_key(report_rel: str) -> str:
    readable = legacy_marker_key(report_rel).strip("-")[:80]
    digest = hashlib.sha256(report_rel.encode("utf-8")).hexdigest()
    return f"{readable}-{digest}" if readable else digest


def marker_lines(kind: str, report_rel: str, *, legacy: bool = False) -> tuple[str, str]:
    key = legacy_marker_key(report_rel) if legacy else marker_key(report_rel)
    begin = f"<!-- BEGIN harness-engineering:growth:{kind}:{key} -->"
    end = f"<!-- END harness-engineering:growth:{kind}:{key} -->"
    return begin, end


def managed_block(kind: str, report_rel: str, body: str) -> str:
    begin, end = marker_lines(kind, report_rel)
    return f"{begin}\n{body.rstrip()}\n{end}\n"


def upsert_block(text: str, block: str) -> str:
    first = block.splitlines()[0]
    last = block.splitlines()[-1]
    if first in text and last in text:
        before, rest = text.split(first, 1)
        _old, after = rest.split(last, 1)
        return before.rstrip() + "\n\n" + block.rstrip() + "\n" + after.lstrip("\n")
    return text.rstrip() + "\n\n" + block


def remove_marked_block(
    text: str,
    first: str,
    last: str,
    *,
    expected_report_rel: str = "",
) -> str:
    if first not in text or last not in text:
        return text
    before, rest = text.split(first, 1)
    old, after = rest.split(last, 1)
    if expected_report_rel and f"`{expected_report_rel}`" not in old:
        return text
    if after.strip():
        return before.rstrip() + "\n\n" + after.lstrip("\n")
    return before.rstrip() + "\n"


def remove_block(text: str, kind: str, report_rel: str) -> str:
    first, last = marker_lines(kind, report_rel)
    updated = remove_marked_block(text, first, last)
    legacy_first, legacy_last = marker_lines(kind, report_rel, legacy=True)
    if (legacy_first, legacy_last) != (first, last):
        updated = remove_marked_block(
            updated,
            legacy_first,
            legacy_last,
            expected_report_rel=report_rel,
        )
    return updated


def knowledge_base(layout: Phase0Layout, path: Path, template: str) -> tuple[str, bool]:
    if path.is_file():
        return path.read_text(encoding="utf-8"), True
    source = template_path(layout, template)
    return (source.read_text(encoding="utf-8") if source.is_file() else ""), False


def resolve_report(layout: Phase0Layout, raw: str) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    candidates = [path] if path.is_absolute() else [
        Path.cwd() / path,
        layout.product_root / path,
        layout.growth_reports_dir / path,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return candidates[0].resolve()


def parse_items(text: str) -> list[dict[str, str]]:
    parts = re.split(r"(?m)^###\s+(G-\d+)\s*$", text)
    items: list[dict[str, str]] = []
    for idx in range(1, len(parts), 2):
        item_id = parts[idx].strip()
        body = parts[idx + 1] if idx + 1 < len(parts) else ""
        fields: dict[str, str] = {"id": item_id}
        for line in body.splitlines():
            match = re.match(r"^\s*-\s+\*\*(来源|原文摘要|建议分类|人工决定|处理结果)\*\*[：:]\s*(.*?)\s*$", line)
            if match:
                value = match.group(2).strip().strip("`")
                fields[match.group(1)] = value
        if len(fields) > 1:
            items.append(fields)
    return items


def classify(item: dict[str, str]) -> set[str]:
    decision = item.get("人工决定", "").strip()
    lowered = decision.lower()
    if not decision or "待定" in decision:
        return set()
    if any(token in lowered for token in ("ignore", "ignored")) or any(token in decision for token in ("忽略", "不采纳", "不适用")):
        return {"ignore"}

    categories: set[str] = set()
    if "context" in lowered or any(token in decision for token in ("CONTEXT", "上下文", "产品事实", "默认行为", "既有抽象", "禁动")):
        categories.add("context")
    if "lesson" in lowered or any(token in decision for token in ("LESSONS", "教训", "失败", "误判", "返工")):
        categories.add("lesson")
    if "tech-debt" in lowered or "debt" in lowered or "技术债" in decision:
        categories.add("tech-debt")
    if any(token in lowered for token in ("architecture", "adr")) or any(token in decision for token in ("架构", "模块边界", "契约")):
        categories.add("architecture")
    return categories


def reviewed_items(text: str, allow_pending: bool) -> list[dict[str, str]]:
    items = []
    for item in parse_items(text):
        pending = "待定" in item.get("人工决定", "") or "待处理" in item.get("处理结果", "")
        if pending and not allow_pending:
            continue
        cats = classify(item)
        if not cats or "ignore" in cats:
            continue
        enriched = dict(item)
        enriched["categories"] = ",".join(sorted(cats))
        items.append(enriched)
    return items


def build_context_body(layout: Phase0Layout, report: Path, items: list[dict[str, str]]) -> str:
    report_rel = layout.rel(report)
    context_items = [
        item
        for item in items
        if any(cat in item["categories"].split(",") for cat in ("context", "architecture"))
    ]
    lines = [
        f"## Growth 沉淀 — {report.name}",
        "",
        f"- **来源报告**：`{report_rel}`",
        "- **沉淀原则**：只写入人工 review 后确认会影响后续默认行为、架构边界或复用约束的成长项。",
        "",
    ]
    if not context_items:
        lines.append("暂无需要写入 CONTEXT 的成长项。")
        return "\n".join(lines)

    lines.extend(["| ID | 分类 | 来源证据 | 摘要 | 处理结果 |", "|----|------|----------|------|----------|"])
    for item in context_items:
        lines.append(
            f"| `{md_escape(item.get('id'))}` | {md_escape(item.get('人工决定'))} | `{md_escape(item.get('来源'))}` | {md_escape(item.get('原文摘要'))} | {md_escape(item.get('处理结果') or '已确认')} |"
        )
    return "\n".join(lines)


def build_lessons_body(layout: Phase0Layout, report: Path, items: list[dict[str, str]]) -> str:
    report_rel = layout.rel(report)
    lesson_items = [
        item
        for item in items
        if any(cat in item["categories"].split(",") for cat in ("lesson", "tech-debt"))
    ]
    lines = [
        f"## Growth 教训 / 技术债沉淀 — {report.name}",
        "",
        f"- **来源报告**：`{report_rel}`",
        "- **沉淀原则**：只记录跨任务可能复现的失败、误判、返工原因或需要排期的技术债入口。",
        "",
    ]
    if not lesson_items:
        lines.append("暂无需要写入 LESSONS 的成长项。")
        return "\n".join(lines)

    lines.extend(["| ID | 分类 | 来源证据 | 摘要 | 下次处理 |", "|----|------|----------|------|----------|"])
    for item in lesson_items:
        lines.append(
            f"| `{md_escape(item.get('id'))}` | {md_escape(item.get('人工决定'))} | `{md_escape(item.get('来源'))}` | {md_escape(item.get('原文摘要'))} | {md_escape(item.get('处理结果') or '按 review 结论执行')} |"
        )
    return "\n".join(lines)


def apply_review(layout: Phase0Layout, report: Path | None, allow_pending: bool) -> dict[str, object]:
    target = report or latest_report(layout)
    if target is None or not target.is_file():
        return {"ok": False, "reason": "GROWTH_REPORT_NOT_FOUND"}
    text = target.read_text(encoding="utf-8", errors="ignore")
    pending_count = len(PENDING_RE.findall(text))
    if pending_count and not allow_pending:
        return {
            "ok": False,
            "reason": f"GROWTH_REVIEW_PENDING: {pending_count} pending fields; rerun with --allow-pending for draft apply",
        }

    report_rel = layout.rel(target)
    items = reviewed_items(text, allow_pending)
    context_items = [
        item
        for item in items
        if any(cat in item["categories"].split(",") for cat in ("context", "architecture"))
    ]
    lesson_items = [
        item
        for item in items
        if any(cat in item["categories"].split(",") for cat in ("lesson", "tech-debt"))
    ]

    context_text, context_exists = knowledge_base(layout, layout.context_file, "context.md")
    lessons_text, lessons_exists = knowledge_base(layout, layout.lessons_file, "lessons.md")
    context_without_legacy = remove_block(context_text, "context", report_rel)
    lessons_without_legacy = remove_block(lessons_text, "lessons", report_rel)
    next_context = (
        upsert_block(
            context_without_legacy,
            managed_block("context", report_rel, build_context_body(layout, target, items)),
        )
        if context_items
        else context_without_legacy
    )
    next_lessons = (
        upsert_block(
            lessons_without_legacy,
            managed_block("lessons", report_rel, build_lessons_body(layout, target, items)),
        )
        if lesson_items
        else lessons_without_legacy
    )
    updated_files: list[str] = []
    if (context_items and not context_exists) or next_context != context_text:
        layout.context_file.parent.mkdir(parents=True, exist_ok=True)
        layout.context_file.write_text(next_context, encoding="utf-8")
        updated_files.append(layout.rel(layout.context_file))
    if (lesson_items and not lessons_exists) or next_lessons != lessons_text:
        layout.lessons_file.parent.mkdir(parents=True, exist_ok=True)
        layout.lessons_file.write_text(next_lessons, encoding="utf-8")
        updated_files.append(layout.rel(layout.lessons_file))
    return {
        "ok": True,
        "reason": "GROWTH_REVIEW_APPLIED",
        "report": report_rel,
        "pending": pending_count,
        "applied_items": len(items),
        "updated_files": updated_files,
        "context_file": layout.rel(layout.context_file),
        "lessons_file": layout.rel(layout.lessons_file),
    }
