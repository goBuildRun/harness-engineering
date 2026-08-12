#!/usr/bin/env python3
"""BMAD-to-Work-Item contract parsing, synchronization, and gate checks."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from work_item_providers import (
    ACCEPTANCE_ITEM_RE,
    WorkItemProvider,
    extract_from_task_dir,
    get_provider,
    load_config,
    yaml,
)

def level_requires_work_item(cfg: dict[str, Any], level: str) -> bool:
    req = cfg.get("requirements") or {}
    return bool(req.get(level, level != "L1"))


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3 or yaml is None:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1].strip()) or {}
    except Exception:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), parts[2].lstrip("\n")


def first_heading(body: str, fallback: str) -> str:
    for line in body.splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return fallback


def section_text(body: str, names: tuple[str, ...], max_chars: int = 420) -> str:
    name_re = "|".join(re.escape(name) for name in names)
    pattern = re.compile(rf"^##+\s*(?:{name_re})\s*$", re.MULTILINE | re.IGNORECASE)
    match = pattern.search(body)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^##+\s+", body[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(body)
    text = re.sub(r"\s+", " ", body[start:end]).strip()
    return text[:max_chars].strip()


def section_bullets(body: str, names: tuple[str, ...], limit: int = 5) -> list[str]:
    bullets: list[str] = []
    name_re = "|".join(re.escape(name) for name in names)
    pattern = re.compile(rf"^##+\s*(?:{name_re})\s*$", re.MULTILINE | re.IGNORECASE)
    match = pattern.search(body)
    if not match:
        return []
    start = match.end()
    next_heading = re.search(r"^##+\s+", body[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(body)
    section = body[start:end]
    for line in section.splitlines():
        m = re.match(r"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.+?)\s*$", line)
        if m:
            bullets.append(m.group(1).strip())
        if len(bullets) >= limit:
            break
    return bullets


def product_root_for(path: Path) -> Path:
    for parent in (path.parent, *path.parents):
        if parent.name == "harness-workspace":
            return parent.parent
        if (parent / "harness-workspace/project.yaml").is_file():
            return parent
    return path.parent


def rel_to_product(path: Path, product_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(product_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def build_work_item_note(spec_path: Path, acceptance_title: str, assignee: str = "") -> str:
    raw = spec_path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(raw)
    product_root = product_root_for(spec_path)
    spec_rel = rel_to_product(spec_path, product_root)
    spec_title = first_heading(body, spec_path.stem)
    goal = section_text(body, ("目标", "产品目标", "项目目标", "背景", "需求背景", "功能描述")) or spec_title
    non_goals = section_bullets(body, ("非目标", "Non-goals", "Out of Scope"), limit=4)
    spec_level = str(meta.get("spec_level") or meta.get("bmad_level") or "").strip() or "-"
    bmad_skills = meta.get("bmad_skills") or []
    if isinstance(bmad_skills, str):
        bmad_skills = [bmad_skills]
    skill_text = ", ".join(str(s) for s in bmad_skills if str(s).strip()) or "-"
    exec_hint = "harness-workspace/planning/exec-plans/active/<待补>"
    task_hint = "harness-workspace/planning/tasks/<待 Planning Gate 后回填>"

    lines = [
        "## 目标",
        goal,
        "",
        "## 范围",
        f"- In scope: `{spec_rel}` 中与本验收项相关的交付",
        "- Out of scope: " + ("；".join(non_goals) if non_goals else "见 Product Spec 非目标"),
        "",
        "## Harness Links",
        f"- Product Spec: `{spec_rel}`",
        f"- Exec Plan: `{exec_hint}`",
        f"- Task Package: `{task_hint}`",
        "- Context: `harness-workspace/knowledge/CONTEXT.md`",
        "",
        "## Gate",
        f"- BMAD Planning: pending（level={spec_level}; skills={skill_text}）",
        "- Planning Gate: pending",
        "- Harness Execution: not_started",
        *([f"- Assignee: `{assignee}`"] if assignee else []),
        "",
        "## 验收标准摘要",
        f"- [ ] {acceptance_title}",
        "",
        "## 管理原则",
        "- Work Item 只承载状态、负责人、讨论和链接；完整 BMAD 产物以产品仓库为真相源。",
    ]
    return "\n".join(lines).rstrip()


def work_item_drafts_from_spec(spec_path: Path, assignee: str = "") -> list[dict[str, Any]]:
    text = spec_path.read_text(encoding="utf-8")
    product_root = product_root_for(spec_path)
    spec_rel = rel_to_product(spec_path, product_root)
    drafts: list[dict[str, Any]] = []
    for index, match in enumerate(ACCEPTANCE_ITEM_RE.finditer(text), start=1):
        title = match.group(1).strip()
        drafts.append(
            {
                "draft_id": f"D{index:03d}",
                "title": title,
                "assignee": assignee,
                "source": spec_rel,
                "contract": "bmad-work-item-v1",
                "note": build_work_item_note(spec_path, title, assignee),
            }
        )
    return drafts


def apply_assignee(provider: WorkItemProvider, assignee: str) -> None:
    if assignee and hasattr(provider, "assignee_id"):
        setattr(provider, "assignee_id", assignee)


def sync_spec_markdown(provider: WorkItemProvider, spec_path: Path, assignee: str = "") -> tuple[int, str]:
    text = spec_path.read_text(encoding="utf-8")
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        title = match.group(1).strip()
        note = build_work_item_note(spec_path, title, assignee)
        item = provider.create(title=title, note=note)
        count += 1
        return f"- [ ] {title} #{item.id}"

    new_text = ACCEPTANCE_ITEM_RE.sub(repl, text)
    if new_text != text:
        spec_path.write_text(new_text, encoding="utf-8")
    return count, f"synced {count} items with bmad-work-item-v1 contract"


def gate_check(harness_root: Path, level: str, task_dir: str | None) -> dict[str, Any]:
    cfg = load_config(harness_root)
    provider = get_provider(harness_root)
    failures: list[str] = []
    work_item: dict[str, Any] | None = None

    if not level_requires_work_item(cfg, level):
        return {"ok": True, "failures": [], "work_item": None, "provider": provider.name}

    if not task_dir:
        return {"ok": False, "failures": ["NO_TASK_DIR_FOR_WORK_ITEM"], "work_item": None}

    td = Path(task_dir)
    wi_id, spec = extract_from_task_dir(td)
    if not wi_id:
        failures.append("NO_WORK_ITEM_ID: 00-任务卡.md 须填写当前产品 provider 的 Work Item ID（L2/L3 必填）")
    else:
        ok, reason = provider.verify(wi_id)
        if not ok:
            failures.append(reason)
        else:
            work_item = {
                "provider": provider.name,
                "id": wi_id,
                "product_spec": spec,
                "verified": ok,
                "verify_reason": reason,
            }

    return {"ok": not failures, "failures": failures, "work_item": work_item, "provider": provider.name}
