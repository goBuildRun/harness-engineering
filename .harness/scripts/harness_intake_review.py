#!/usr/bin/env python3
"""Review and persist brownfield intake evidence into product knowledge."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness_knowledge import ensure
from workspace_paths import Phase0Layout


def md_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\n", " ")
    )


def review_status(layout: Phase0Layout) -> dict[str, Any]:
    reports = sorted(layout.intake_reports_dir.glob("*-INTAKE.md")) if layout.intake_reports_dir.is_dir() else []
    pending: list[str] = []
    reviewed: list[str] = []
    for report in reports:
        text = report.read_text(encoding="utf-8", errors="ignore")
        unchecked = unchecked_checklist_count(text)
        if unchecked:
            pending.append(f"{layout.rel(report)}:{unchecked}")
        else:
            reviewed.append(layout.rel(report))
    return {"reports": len(reports), "pending": pending, "reviewed": reviewed}


def unchecked_checklist_count(text: str) -> int:
    return len(re.findall(r"(?m)^\s*-\s+\[ \]\s+", text))


def latest_report(layout: Phase0Layout) -> Path | None:
    reports = sorted(layout.intake_reports_dir.glob("*-INTAKE*.md")) if layout.intake_reports_dir.is_dir() else []
    return reports[-1] if reports else None


def clean_table_cell(cell: str) -> str:
    value = cell.strip().replace("<PIPE>", "|")
    if len(value) >= 2 and value.startswith("`") and value.endswith("`") and value.count("`") == 2:
        return value[1:-1].strip()
    return value


def table_rows(text: str, heading: str, stop_pattern: str) -> list[list[str]]:
    start = text.find(heading)
    if start < 0:
        return []
    chunk = text[start:]
    stop = re.search(stop_pattern, chunk[len(heading) :])
    if stop:
        chunk = chunk[: len(heading) + stop.start()]
    rows: list[list[str]] = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        safe = line.replace(r"\|", "<PIPE>")
        cells = [clean_table_cell(c) for c in safe.strip("|").split("|")]
        if cells and cells[0] not in {"路径", "来源", "类别"}:
            rows.append(cells)
    return rows


def bullet_rows(text: str, heading: str, stop_pattern: str) -> list[tuple[str, str]]:
    start = text.find(heading)
    if start < 0:
        return []
    chunk = text[start:]
    stop = re.search(stop_pattern, chunk[len(heading) :])
    if stop:
        chunk = chunk[: len(heading) + stop.start()]
    rows: list[tuple[str, str]] = []
    for line in chunk.splitlines():
        match = re.match(r"^-\s+`([^`]+)`\s+—\s+(.+)$", line.strip())
        if match:
            rows.append((match.group(1), match.group(2)))
    return rows


def product_owned(scope: str) -> bool:
    return "product-owned" in scope and "debt=track" in scope


def upstream_reference(scope: str) -> bool:
    return "upstream-reference" in scope


def managed_block(kind: str, report_rel: str, body: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_.-]+", "-", report_rel)
    begin = f"<!-- BEGIN harness-engineering:intake:{kind}:{key} -->"
    end = f"<!-- END harness-engineering:intake:{kind}:{key} -->"
    return f"{begin}\n{body.rstrip()}\n{end}\n"


def upsert_block(text: str, block: str) -> str:
    first = block.splitlines()[0]
    last = block.splitlines()[-1]
    if first in text and last in text:
        before, rest = text.split(first, 1)
        _old, after = rest.split(last, 1)
        return before.rstrip() + "\n\n" + block.rstrip() + "\n" + after.lstrip("\n")
    return text.rstrip() + "\n\n" + block


def build_context_body(layout: Phase0Layout, report: Path, text: str) -> str:
    report_rel = layout.rel(report)
    scopes = table_rows(text, "### Intake 归属策略", r"\n### 目录")
    docs = table_rows(text, "## 2. 现有文档清单", r"\n## 3\. ")
    entries = table_rows(text, "### 关键代码入口候选", r"\n### 技术栈信号|\n### 文件类型分布|\n## 4\. ")
    stacks = table_rows(text, "### 技术栈信号", r"\n### 文件类型分布|\n## 4\. ")
    tests = bullet_rows(text, "## 4. 构建、测试与 CI 信号", r"\n## 5\. ")

    product_scopes = [r for r in scopes if len(r) >= 5 and r[1] == "product-owned"]
    upstream_scopes = [r for r in scopes if len(r) >= 5 and r[1] == "upstream-reference"]
    product_docs = [r for r in docs if len(r) >= 3 and product_owned(r[1])][:12]
    upstream_docs = [r for r in docs if len(r) >= 3 and upstream_reference(r[1])][:12]
    product_entries = [r for r in entries if len(r) >= 3 and product_owned(r[1])][:24]
    product_stacks = [r for r in stacks if len(r) >= 4 and product_owned(r[1])][:20]
    product_tests = [(p, s) for p, s in tests if product_owned(s)][:20]

    lines = [
        f"## Intake 沉淀 — {report.name}",
        "",
        f"- **来源报告**：`{report_rel}`",
        "- **沉淀原则**：上游参考只沉淀核心逻辑、关键特性和可复用约束；自研范围沉淀产品事实、运行方式、测试信号和技术债入口。",
        "",
        "### 归属边界",
        "",
        "| 路径 | 角色 | Review 策略 | 技术债策略 | 说明 |",
        "|------|------|-------------|------------|------|",
    ]
    for row in scopes:
        if len(row) >= 5:
            lines.append(f"| `{md_escape(row[0])}` | {md_escape(row[1])} | {md_escape(row[2])} | {md_escape(row[3])} | {md_escape(row[4])} |")

    lines.extend(["", "### 上游参考理解", ""])
    if upstream_scopes:
        for row in upstream_scopes:
            lines.append(f"- `{md_escape(row[0])}`：{md_escape(row[4])}")
    else:
        lines.append("- 暂无显式上游参考 scope。")
    for row in upstream_docs:
        lines.append(f"  - 文档证据 `{md_escape(row[0])}`：{md_escape(row[2])}")

    lines.extend(["", "### 自研产品范围", ""])
    if product_scopes:
        for row in product_scopes:
            lines.append(f"- `{md_escape(row[0])}`：{md_escape(row[4])}")
    else:
        lines.append("- 默认 scope 按产品自有内容处理。")
    for row in product_docs:
        lines.append(f"  - 文档证据 `{md_escape(row[0])}`：{md_escape(row[2])}")

    lines.extend(["", "### 自研关键代码入口", ""])
    if product_entries:
        for row in product_entries:
            lines.append(f"- `{md_escape(row[0])}`：{md_escape(row[2])}")
    else:
        lines.append("- 暂无自研关键代码入口候选。")

    lines.extend(["", "### 技术栈与运行信号", ""])
    if product_stacks:
        for row in product_stacks:
            lines.append(f"- `{md_escape(row[0])}`：{md_escape(row[2])}；{md_escape(row[3])}")
    else:
        lines.append("- 暂无自研范围技术栈信号。")

    lines.extend(["", "### 测试与验证信号", ""])
    if product_tests:
        for path, _scope in product_tests:
            lines.append(f"- `{md_escape(path)}`")
    else:
        lines.append("- 暂无自研范围测试/CI 信号。")
    return "\n".join(lines)


def build_lessons_body(layout: Phase0Layout, report: Path, text: str) -> str:
    report_rel = layout.rel(report)
    markers = table_rows(text, "### LESSONS / 技术债候选", r"\n### 架构候选")
    product_markers = [r for r in markers if len(r) >= 4 and product_owned(r[1])]
    lines = [
        f"## Intake 技术债候选 — {report.name}",
        "",
        f"- **来源报告**：`{report_rel}`",
        "- **规则**：只记录 `product-owned / debt=track` 范围；`upstream-reference / debt=ignore` 不进入产品技术债。",
        "",
    ]
    if not product_markers:
        lines.append("暂无自研范围技术债候选。")
        return "\n".join(lines)

    lines.extend(["| 来源 | 行 | 候选 | 建议处理 |", "|------|----|------|----------|"])
    for row in product_markers[:40]:
        lines.append(f"| `{md_escape(row[0])}` | {md_escape(row[2])} | {md_escape(row[3])} | 人工确认后保留为 LESSON 或创建 Work Item |")
    return "\n".join(lines)


def build_reference_body(layout: Phase0Layout, report: Path, text: str) -> str:
    report_rel = layout.rel(report)
    scopes = table_rows(text, "### Intake 归属策略", r"\n### 目录")
    docs = table_rows(text, "## 2. 现有文档清单", r"\n## 3\. ")
    entries = table_rows(text, "### 关键代码入口候选", r"\n### 技术栈信号|\n### 文件类型分布|\n## 4\. ")
    stacks = table_rows(text, "### 技术栈信号", r"\n### 文件类型分布|\n## 4\. ")
    tests = bullet_rows(text, "## 4. 构建、测试与 CI 信号", r"\n## 5\. ")

    upstream_scopes = [r for r in scopes if len(r) >= 5 and upstream_reference(r[1])]
    upstream_docs = [r for r in docs if len(r) >= 3 and upstream_reference(r[1])][:24]
    upstream_entries = [r for r in entries if len(r) >= 3 and upstream_reference(r[1])][:32]
    upstream_stacks = [r for r in stacks if len(r) >= 4 and upstream_reference(r[1])][:24]
    upstream_tests = [(p, s) for p, s in tests if upstream_reference(s)][:24]

    lines = [
        f"## Intake 上游参考系统 — {report.name}",
        "",
        f"- **来源报告**：`{report_rel}`",
        "- **沉淀目标**：把重度依赖系统的核心逻辑、扩展点、代码入口和禁改边界沉淀为长期知识；上游技术债不进入产品技术债。",
        "",
        "### 上游系统边界",
        "",
    ]
    if upstream_scopes:
        lines.extend(["| 路径 | Review 策略 | 技术债策略 | 说明 |", "|------|-------------|------------|------|"])
        for row in upstream_scopes:
            lines.append(f"| `{md_escape(row[0])}` | {md_escape(row[2])} | {md_escape(row[3])} | {md_escape(row[4])} |")
    else:
        lines.append("暂无显式 `upstream-reference` scope。")

    lines.extend(["", "### 必读文档入口", ""])
    if upstream_docs:
        for row in upstream_docs:
            lines.append(f"- `{md_escape(row[0])}`：{md_escape(row[2])}")
    else:
        lines.append("- 暂无上游文档入口。")

    lines.extend(["", "### 关键代码入口候选", ""])
    if upstream_entries:
        for row in upstream_entries:
            lines.append(f"- `{md_escape(row[0])}`：{md_escape(row[2])}")
    else:
        lines.append("- 暂无上游关键代码入口候选。")

    lines.extend(["", "### 代码与运行入口信号", ""])
    if upstream_stacks:
        for row in upstream_stacks:
            lines.append(f"- `{md_escape(row[0])}`：{md_escape(row[2])}；{md_escape(row[3])}")
    else:
        lines.append("- 暂无上游技术栈入口信号。")

    lines.extend(["", "### 上游测试/验证入口", ""])
    if upstream_tests:
        for path, _scope in upstream_tests:
            lines.append(f"- `{md_escape(path)}`")
    else:
        lines.append("- 暂无上游测试入口信号。")

    lines.extend(
        [
            "",
            "### 深读要求",
            "",
            "- 对每个上游系统至少沉淀：产品角色、核心机制、扩展点、禁改边界、关键代码入口、升级风险。",
            "- 若产品在上游目录内承载自研子工程，必须在 `intake.scopes` 用更长路径标为 `product-owned`。",
            "- 本区块是自动证据索引；语义级摘要应由 Agent/架构负责人深读后写入本文件的人工维护区。",
        ]
    )
    return "\n".join(lines)


def apply_review(layout: Phase0Layout, report: Path | None, allow_pending: bool) -> dict[str, Any]:
    target = report or latest_report(layout)
    if target is None or not target.is_file():
        return {"ok": False, "reason": "INTAKE_REPORT_NOT_FOUND"}
    text = target.read_text(encoding="utf-8", errors="ignore")
    unchecked = unchecked_checklist_count(text)
    if unchecked and not allow_pending:
        return {"ok": False, "reason": f"INTAKE_REVIEW_PENDING: {unchecked} unchecked items; rerun with --allow-pending for draft apply"}

    ensure(layout)
    report_rel = layout.rel(target)
    context_block = managed_block("context", report_rel, build_context_body(layout, target, text))
    lessons_block = managed_block("lessons", report_rel, build_lessons_body(layout, target, text))
    reference_block = managed_block("references", report_rel, build_reference_body(layout, target, text))

    context_text = layout.context_file.read_text(encoding="utf-8") if layout.context_file.is_file() else ""
    lessons_text = layout.lessons_file.read_text(encoding="utf-8") if layout.lessons_file.is_file() else ""
    reference_text = layout.reference_systems_file.read_text(encoding="utf-8") if layout.reference_systems_file.is_file() else ""
    layout.context_file.write_text(upsert_block(context_text, context_block), encoding="utf-8")
    layout.lessons_file.write_text(upsert_block(lessons_text, lessons_block), encoding="utf-8")
    layout.reference_systems_file.write_text(upsert_block(reference_text, reference_block), encoding="utf-8")
    return {
        "ok": True,
        "reason": "INTAKE_REVIEW_APPLIED",
        "report": report_rel,
        "unchecked": unchecked,
        "context_file": layout.rel(layout.context_file),
        "lessons_file": layout.rel(layout.lessons_file),
        "reference_systems_file": layout.rel(layout.reference_systems_file),
    }
