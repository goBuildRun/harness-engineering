#!/usr/bin/env python3
"""Project knowledge artifact bootstrap for the Team Product R&D Harness."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from harness_output import dump_json
from harness_knowledge_parse import (
    artifact_kind,
    artifact_summary,
    compact,
    field_value,
    first_heading,
    markdown_files,
    md_cell,
    meta_list,
    parse_front_matter,
    raw_bmad_artifacts,
    read_text,
    section_bullets,
    section_summary,
    section_text,
)
from workspace_paths import Phase0Layout, load_layout

PLANNING_BLOCK_BEGIN = "<!-- BEGIN harness-engineering:planning-context -->"
PLANNING_BLOCK_END = "<!-- END harness-engineering:planning-context -->"


def emit(decision: str, reason: str, **extra) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def template_path(layout: Phase0Layout, name: str) -> Path:
    return layout.harness_root / ".harness" / "templates" / name


def copy_if_missing(src: Path, dst: Path) -> bool:
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def ensure(layout: Phase0Layout) -> list[str]:
    created: list[str] = []
    for directory in (
        layout.knowledge_root,
        layout.summaries_dir,
        layout.progress_dir,
        layout.test_reports_dir,
        layout.review_reports_dir,
        layout.growth_reports_dir,
        layout.intake_reports_dir,
    ):
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            created.append(layout.rel(directory) + "/")

    pairs = (
        (template_path(layout, "context.md"), layout.context_file),
        (template_path(layout, "lessons.md"), layout.lessons_file),
        (template_path(layout, "reference-systems.md"), layout.reference_systems_file),
    )
    for src, dst in pairs:
        if src.is_file() and copy_if_missing(src, dst):
            created.append(layout.rel(dst))
    return created


def build_planning_context_body(layout: Phase0Layout) -> tuple[str, dict[str, int]]:
    specs = markdown_files(layout.product_specs)
    exec_plans = [*markdown_files(layout.exec_plans_active), *markdown_files(layout.exec_plans_completed)]
    task_cards = sorted(p / "00-任务卡.md" for p in layout.tasks.iterdir() if p.is_dir() and (p / "00-任务卡.md").is_file()) if layout.tasks.is_dir() else []
    raw_artifacts = raw_bmad_artifacts(layout)

    lines = [
        "## BMAD Planning 规划沉淀",
        "",
        f"- **产品**：{layout.product_name}（`{layout.product_id}`）",
        f"- **规划根**：`{layout.rel(layout.planning_root)}`",
        f"- **BMAD 原生输出**：`{layout.rel(layout.bmad_output_root)}`",
        "- **沉淀规则**：BMAD Planning 形成的产品目标、范围、验收、架构/方案和任务边界，必须进入本区块，作为后续 Agent 默认上下文。",
        "- **维护方式**：本区块由 `harness_knowledge.sh sync-planning` 管理；需要补充语义解释时，可在本文件其他人工维护区补充。",
        "",
        "### 产品规格 / 产品蓝图",
        "",
    ]

    if specs:
        lines.extend(["| 规格 | 级别 | BMAD 技能 | 目标/摘要 | 验收标准 | 非目标 |", "|------|------|-----------|-----------|----------|--------|"])
        for path in specs:
            raw = read_text(path)
            meta, body = parse_front_matter(raw)
            title = first_heading(body, path.stem)
            level = meta.get("spec_level") or meta.get("bmad_level") or "-"
            skills = ", ".join(meta_list(meta, "bmad_skills")) or "-"
            goal = (
                section_summary(body, ("目标", "产品目标", "项目目标", "背景", "需求背景", "功能描述"))
                or title
            )
            acceptance = "；".join(section_bullets(body, ("验收标准", "Acceptance Criteria"))) or "-"
            non_goals = "；".join(section_bullets(body, ("非目标", "Non-goals", "Out of Scope"))) or "-"
            lines.append(
                f"| [`{md_cell(title)}`]({layout.rel(path)}) | {md_cell(level)} | {md_cell(skills)} | {md_cell(goal)} | {md_cell(acceptance)} | {md_cell(non_goals)} |"
            )
    else:
        lines.append("暂无已映射入库的产品规格。")

    lines.extend(["", "### 架构 / Solutioning / 执行计划", ""])
    if exec_plans:
        lines.extend(["| 执行计划 | 关联规格 | BMAD 技能 | 目标 | 就绪检查 / 架构结论 | 验收标准 |", "|----------|----------|-----------|------|----------------------|----------|"])
        for path in exec_plans:
            raw = read_text(path)
            meta, body = parse_front_matter(raw)
            title = first_heading(body, path.stem)
            linked = meta.get("linked_spec") or "-"
            skills = ", ".join(meta_list(meta, "bmad_skills")) or "-"
            goal = section_summary(body, ("目标", "架构目标", "方案目标")) or title
            readiness = section_summary(body, ("就绪检查结论", "架构结论", "方案", "技术方案"), 220) or "-"
            acceptance = "；".join(section_bullets(body, ("验收标准", "Acceptance Criteria"))) or "-"
            lines.append(
                f"| [`{md_cell(title)}`]({layout.rel(path)}) | `{md_cell(linked)}` | {md_cell(skills)} | {md_cell(goal)} | {md_cell(readiness)} | {md_cell(acceptance)} |"
            )
    else:
        lines.append("暂无已映射入库的执行计划。")

    lines.extend(["", "### 任务边界 / Work Item", ""])
    if task_cards:
        lines.extend(["| 任务目录 | 标题 | 编号 | 风险 | 状态 | 规格 / 执行计划 | 当前结论 |", "|----------|------|------|------|------|-----------------|----------|"])
        for card in task_cards:
            text = read_text(card)
            title = field_value(text, "任务标题") or card.parent.name
            work_item = field_value(text, "任务编号") or "-"
            status = field_value(text, "当前状态") or "-"
            risk = field_value(text, "风险等级") or "-"
            spec = re.search(r"产品规格链接[：:\s]*`?([^`\n]+)`?", text)
            plan = re.search(r"执行计划链接[：:\s]*`?([^`\n]+)`?", text)
            links = []
            if spec:
                links.append(f"spec `{compact(spec.group(1), 80)}`")
            if plan:
                links.append(f"plan `{compact(plan.group(1), 80)}`")
            conclusion = section_summary(text, ("当前结论摘要",), 180) or "-"
            lines.append(
                f"| [`{md_cell(card.parent.name)}`]({layout.rel(card.parent)}) | {md_cell(title)} | `{md_cell(work_item)}` | {md_cell(risk)} | {md_cell(status)} | {md_cell('；'.join(links) or '-')} | {md_cell(conclusion)} |"
            )
    else:
        lines.append("暂无已入库的任务卡。")

    lines.extend(["", "### BMAD 原生规划 / 设计产物摘要", ""])
    if raw_artifacts:
        lines.extend(["| 原生产物 | 类型 | 摘要 |", "|----------|------|------|"])
        for path in raw_artifacts[:20]:
            text = read_text(path, 80_000)
            meta, body = parse_front_matter(text)
            title = first_heading(body, path.stem)
            lines.append(
                f"| [`{md_cell(title)}`]({layout.rel(path)}) | {md_cell(artifact_kind(path, meta, title))} | {md_cell(artifact_summary(body, title))} |"
            )
    else:
        lines.append("暂无 BMAD 原生 planning-artifacts / design-artifacts，或已全部映射到 `planning/`。")

    counts = {
        "product_specs": len(specs),
        "exec_plans": len(exec_plans),
        "task_cards": len(task_cards),
        "bmad_artifacts": len(raw_artifacts),
    }
    return "\n".join(lines), counts


def planning_block(body: str) -> str:
    return f"{PLANNING_BLOCK_BEGIN}\n{body.rstrip()}\n{PLANNING_BLOCK_END}\n"


def upsert_planning_block(text: str, block: str) -> str:
    if PLANNING_BLOCK_BEGIN in text and PLANNING_BLOCK_END in text:
        before, rest = text.split(PLANNING_BLOCK_BEGIN, 1)
        _old, after = rest.split(PLANNING_BLOCK_END, 1)
        return before.rstrip() + "\n\n" + block.rstrip() + "\n" + after.lstrip("\n")
    return text.rstrip() + "\n\n" + block


def sync_planning(layout: Phase0Layout) -> dict[str, Any]:
    ensure(layout)
    body, counts = build_planning_context_body(layout)
    existing = layout.context_file.read_text(encoding="utf-8") if layout.context_file.is_file() else ""
    layout.context_file.write_text(upsert_planning_block(existing, planning_block(body)), encoding="utf-8")
    return {
        "context_file": layout.rel(layout.context_file),
        "planning_root": layout.rel(layout.planning_root),
        "bmad_output_root": layout.rel(layout.bmad_output_root),
        **counts,
    }


def check_planning(layout: Phase0Layout) -> dict[str, Any]:
    body, counts = build_planning_context_body(layout)
    existing = layout.context_file.read_text(encoding="utf-8") if layout.context_file.is_file() else ""
    expected = upsert_planning_block(existing, planning_block(body))
    stale = existing != expected
    return {
        "ok": not stale,
        "reason": "PLANNING_CONTEXT_STALE" if stale else "PLANNING_CONTEXT_OK",
        "context_file": layout.rel(layout.context_file),
        "planning_root": layout.rel(layout.planning_root),
        "bmad_output_root": layout.rel(layout.bmad_output_root),
        "stale": stale,
        **counts,
    }


def paths(layout: Phase0Layout) -> dict[str, str]:
    return {
        "knowledge_root": layout.rel(layout.knowledge_root),
        "context_file": layout.rel(layout.context_file),
        "lessons_file": layout.rel(layout.lessons_file),
        "reference_systems_file": layout.rel(layout.reference_systems_file),
        "summaries_dir": layout.rel(layout.summaries_dir),
        "progress_dir": layout.rel(layout.progress_dir),
        "test_reports_dir": layout.rel(layout.test_reports_dir),
        "review_reports_dir": layout.rel(layout.review_reports_dir),
        "growth_reports_dir": layout.rel(layout.growth_reports_dir),
        "intake_reports_dir": layout.rel(layout.intake_reports_dir),
        "profile": layout.harness_profile,
        "product_name": layout.product_name,
        "task_contract": layout.task_contract,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("cmd", choices=("paths", "ensure", "sync-planning", "check-planning"))
    args = parser.parse_args()

    product_root = args.product_root
    layout = load_layout(
        Path(args.harness_root).resolve(),
        Path(product_root).resolve() if product_root else None,
    )

    if args.cmd == "paths":
        emit("pass", "KNOWLEDGE_PATHS", **paths(layout))
        return 0

    if args.cmd == "ensure":
        created = ensure(layout)
        emit("pass", "KNOWLEDGE_READY", created=created, **paths(layout))
        return 0

    if args.cmd == "sync-planning":
        result = sync_planning(layout)
        emit("pass", "PLANNING_CONTEXT_SYNCED", **result)
        return 0

    result = check_planning(layout)
    emit("pass" if result.pop("ok") else "block", str(result.pop("reason")), **result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
