#!/usr/bin/env python3
"""Mechanical validation for BMAD Planning before AEL execution."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ael_output import dump_json

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from workspace_paths import Phase0Layout, load_layout, normalize_legacy_rel, resolve_phase0_path

PLANNING_AUTHORING_SKILLS_L2_L3 = ("bmad-create-prd", "bmad-correct-course")
PLANNING_VALIDATION_SKILLS_L2_L3 = ("bmad-validate-prd",)
SOLUTIONING_SKILLS_L2 = ("bmad-check-implementation-readiness",)
SOLUTIONING_SKILLS_L3 = ("bmad-create-architecture", "bmad-check-implementation-readiness")
QUICK_FLOW_SKILLS = (
    "bmad-quick-dev",
    "bmad-create-prd",
    "bmad-product-brief",
    "bmad-agent-quick-flow-solo-dev",
)
CHECKBOX_RE = re.compile(r"^\s*-\s+\[[ xX]\]", re.MULTILINE)
BMAD_SKILL_RE = re.compile(r"bmad-[a-z0-9-]+")
TEMPLATE_HINT = ".ael/templates/product-spec.md"


def parse_front_matter(text: str) -> tuple[dict[str, Any] | None, str]:
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    body = parts[2].lstrip("\n")
    raw = parts[1].strip()
    if yaml is None:
        return None, text
    meta = yaml.safe_load(raw) or {}
    if not isinstance(meta, dict):
        return None, text
    return meta, body


def load_meta(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    meta, _ = parse_front_matter(path.read_text(encoding="utf-8"))
    return meta


def skills_list(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("bmad_skills") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(s).strip() for s in raw if str(s).strip()]


def validate_bmad_meta(meta: dict[str, Any] | None, path: Path, *, label: str) -> list[str]:
    fails: list[str] = []
    if not meta:
        fails.append(
            f"BMAD_NO_FRONTMATTER: {label} {path} 须含 YAML front matter（见 {TEMPLATE_HINT}）"
        )
        return fails
    if meta.get("bmad_method") is not True:
        fails.append(f"BMAD_METHOD_FALSE: {label} {path} 须 bmad_method: true")
    skills = skills_list(meta)
    if not skills:
        fails.append(f"BMAD_NO_SKILLS: {label} {path} 须列出 bmad_skills（已执行的 BMAD Method 技能）")
    for skill in skills:
        if not skill.startswith("bmad-"):
            fails.append(f"BMAD_BAD_SKILL: {label} {path} 技能须以 bmad- 开头，发现 {skill}")
    if not meta.get("bmad_completed_at"):
        fails.append(f"BMAD_NO_DATE: {label} {path} 须 bmad_completed_at（工作流完成日期）")
    return fails


def require_skills(meta: dict[str, Any], required: tuple[str, ...], path: Path, label: str) -> list[str]:
    fails: list[str] = []
    have = set(skills_list(meta))
    for skill in required:
        if skill not in have:
            fails.append(f"BMAD_MISSING_SKILL: {label} {path} 须含 {skill}")
    return fails


def require_any_skill(
    meta: dict[str, Any], allowed: tuple[str, ...], path: Path, label: str, purpose: str
) -> list[str]:
    have = set(skills_list(meta))
    if not have.intersection(allowed):
        return [f"BMAD_MISSING_SKILL: {label} {path} 须含其一: {', '.join(allowed)}（{purpose}）"]
    return []


def has_checkboxes(text: str) -> bool:
    return bool(CHECKBOX_RE.search(text))


def extract_link(content: str, label: str) -> str | None:
    pattern = rf"{re.escape(label)}[`：:\s]*`?([^`\n]+)`?"
    match = re.search(pattern, content)
    if not match:
        return None
    value = match.group(1).strip()
    if not value or "..." in value:
        return None
    return value


def parse_workflow_table(content: str) -> list[str]:
    if "## BMAD Method 工作流记录" not in content:
        return []
    section = content.split("## BMAD Method 工作流记录", 1)[1]
    section = section.split("\n##", 1)[0]
    skills: list[str] = []
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        if "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0].lower() in ("阶段", "phase"):
            continue
        skills.extend(BMAD_SKILL_RE.findall("|".join(cells)))
    return skills


def validate_spec_file(path: Path, level: str) -> list[str]:
    fails: list[str] = []
    if not path.is_file():
        return [f"SPEC_MISSING: {path}"]
    text = path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(text)
    fails.extend(validate_bmad_meta(meta, path, label="product-spec"))
    if meta:
        spec_level = str(meta.get("spec_level") or meta.get("bmad_level") or "").upper()
        if spec_level and spec_level != level:
            fails.append(f"SPEC_LEVEL_MISMATCH: {path} spec_level={spec_level} 与 gate {level} 不一致")
        flow = str(meta.get("bmad_flow") or "standard").lower()
        if level == "L1" and flow != "quick":
            fails.append(f"BMAD_L1_FLOW: {path} L1 须 bmad_flow: quick")
        if level != "L1" and flow == "quick":
            fails.append(f"BMAD_QUICK_ON_L23: {path} L2/L3 不得 bmad_flow: quick")
        if level == "L1":
            have = set(skills_list(meta))
            if not have.intersection(QUICK_FLOW_SKILLS):
                fails.append(
                    f"BMAD_L1_SKILL: {path} L1 Quick Flow 须含其一: {', '.join(QUICK_FLOW_SKILLS)}"
                )
        else:
            fails.extend(
                require_any_skill(
                    meta,
                    PLANNING_AUTHORING_SKILLS_L2_L3,
                    path,
                    "Planning",
                    "新建 PRD 或 course-correction 规划入口",
                )
            )
            fails.extend(require_skills(meta, PLANNING_VALIDATION_SKILLS_L2_L3, path, "Planning"))
    if not has_checkboxes(body if meta else text):
        fails.append(f"SPEC_NO_CHECKBOX: {path} 验收标准须含可勾选 `- [ ]` 条目")
    return fails


def validate_exec_plan(
    path: Path, level: str, spec_path: Path | None, layout: Phase0Layout
) -> list[str]:
    fails: list[str] = []
    if not path.is_file():
        rel = layout.rel_phase0(layout.exec_plans_active)
        return [f"EXEC_PLAN_MISSING: {level} 须 {rel}/<功能>.md"]
    meta, _ = parse_front_matter(path.read_text(encoding="utf-8"))
    fails.extend(validate_bmad_meta(meta, path, label="exec-plan"))
    if meta:
        phases = meta.get("bmad_phases") or []
        if isinstance(phases, str):
            phases = [phases]
        if "solutioning" not in [str(p).lower() for p in phases]:
            fails.append(f"BMAD_PHASE: {path} exec-plan bmad_phases 须含 solutioning")
        required = SOLUTIONING_SKILLS_L3 if level == "L3" else SOLUTIONING_SKILLS_L2
        fails.extend(require_skills(meta, required, path, "Solutioning"))
        linked = meta.get("linked_spec")
        if spec_path and linked:
            linked_path = resolve_phase0_path(layout, str(linked))
            if linked_path.resolve() != spec_path.resolve():
                fails.append(
                    f"EXEC_SPEC_LINK: {path} linked_spec 须指向 {layout.rel_phase0(spec_path)}"
                )
    return fails


def validate_task_card(
    task_dir: Path, level: str, layout: Phase0Layout
) -> tuple[list[str], Path | None, Path | None]:
    fails: list[str] = []
    card = task_dir / "00-任务卡.md"
    if not card.is_file():
        return [f"MISSING: {card}"], None, None
    content = card.read_text(encoding="utf-8")
    spec_rel = extract_link(content, "产品规格链接")
    exec_rel = extract_link(content, "执行计划链接")
    spec_path = resolve_phase0_path(layout, spec_rel) if spec_rel else None
    exec_path = resolve_phase0_path(layout, exec_rel) if exec_rel else None

    table_skills = parse_workflow_table(content)
    if level in ("L2", "L3"):
        if not table_skills:
            fails.append("BMAD_WORKFLOW_TABLE: 00-任务卡.md 须含「## BMAD Method 工作流记录」表且填写 bmad-* 技能")
        else:
            for skill in table_skills:
                if not skill.startswith("bmad-"):
                    fails.append(f"BMAD_TABLE_SKILL: 工作流表含非法技能 {skill}")
            if not set(table_skills).intersection(PLANNING_AUTHORING_SKILLS_L2_L3):
                fails.append(
                    "BMAD_TABLE_MISSING: 工作流表须含其一 "
                    + ", ".join(PLANNING_AUTHORING_SKILLS_L2_L3)
                )
            for required in PLANNING_VALIDATION_SKILLS_L2_L3:
                if required not in table_skills:
                    fails.append(f"BMAD_TABLE_MISSING: 工作流表须含 {required}")
            solutioning_required = SOLUTIONING_SKILLS_L3 if level == "L3" else SOLUTIONING_SKILLS_L2
            for required in solutioning_required:
                if required not in table_skills:
                    fails.append(f"BMAD_TABLE_MISSING: 工作流表须含 {required}")
        if not exec_rel:
            fails.append("EXEC_PLAN_LINK: 00-任务卡.md 须填写执行计划链接（非占位 ...）")

    if spec_path:
        fails.extend(validate_spec_file(spec_path, level))
    else:
        fails.append("SPEC_LINK: 00-任务卡.md 须填写产品规格链接")

    if level in ("L2", "L3") and exec_path:
        fails.extend(validate_exec_plan(exec_path, level, spec_path, layout))

    return fails, spec_path, exec_path


def validate_l1(layout: Phase0Layout) -> list[str]:
    fails: list[str] = []
    context = layout.agent_workspace / "context.md"
    specs = sorted(
        p
        for p in layout.product_specs.glob("*.md")
        if p.name not in ("index.md", "_template.md")
    )
    candidates: list[Path] = []
    if context.is_file() and load_meta(context):
        candidates.append(context)
    candidates.extend(specs)
    if not candidates:
        rel = layout.rel(layout.product_specs)
        fails.append(
            f"NO_L1_SPEC: L1 须 {rel}/<功能>.md 或带 BMAD front matter 的 {layout.rel(context)}"
        )
        return fails
    spec_fails: list[str] = []
    for path in candidates:
        spec_fails = validate_spec_file(path, "L1")
        if not spec_fails:
            return []
    fails.extend(spec_fails or ["BMAD_L1_INVALID: 未找到通过 BMAD Quick Flow 校验的规格文件"])
    return fails


def run_gate(level: str, layout: Phase0Layout, task_dir: str | None) -> dict[str, Any]:
    fails: list[str] = []
    if level == "L1":
        fails.extend(validate_l1(layout))
    elif level in ("L2", "L3"):
        if not task_dir:
            fails.append(f"NO_TASK_DIR: L2/L3 须传入 {layout.rel(layout.tasks)}/ 目录")
        else:
            td = Path(task_dir).resolve()
            if not td.is_dir():
                fails.append(f"TASK_DIR_MISSING: {task_dir}")
            else:
                fails.extend(validate_task_card(td, level, layout)[0])
    else:
        fails.append(f"INVALID_LEVEL: {level}")

    if fails:
        return {"decision": "block", "reason": "BMAD_METHOD_GATE: " + "; ".join(fails)}
    return {"decision": "pass", "reason": "BMAD_METHOD_OK: BMAD Method 产出与工作流记录校验通过"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", required=True, choices=["L1", "L2", "L3"])
    parser.add_argument("--ael-root", required=True)
    parser.add_argument("--product-root", default="")
    parser.add_argument("--task-dir", default="")
    args = parser.parse_args()
    if yaml is None:
        dump_json({"decision": "block", "reason": "BMAD_GATE_NO_YAML: pip install pyyaml"})
        return
    layout = load_layout(
        Path(args.ael_root).resolve(),
        Path(args.product_root).resolve() if args.product_root else None,
    )
    result = run_gate(args.level, layout, args.task_dir or None)
    dump_json(result)


if __name__ == "__main__":
    main()
