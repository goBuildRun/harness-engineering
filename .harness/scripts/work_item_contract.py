#!/usr/bin/env python3
"""BMAD-to-Work-Item contract parsing, synchronization, and gate checks."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from production_evidence_policy import from_spec_metadata
from work_item_providers import (
    ACCEPTANCE_ITEM_RE,
    WorkItemProvider,
    active_product_root,
    extract_from_task_dir,
    get_provider,
    load_config,
    provider_expected_project_id,
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


def parent_work_item_id_from_spec(spec_path: Path) -> str:
    return str(work_item_contract_from_spec(spec_path)["parent_id"] or "")


def _metadata_string(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field}_INVALID: expected string, got {type(value).__name__}")
    return value.strip()


def work_item_contract_from_spec(
    spec_path: Path,
    require_l3_type: bool = False,
    alternate_parent_id: str = "",
) -> dict[str, Any]:
    if not spec_path.is_file():
        raise ValueError(f"WORK_ITEM_SPEC_MISSING: {spec_path}")
    text = spec_path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) < 3 or yaml is None:
            raise ValueError(f"WORK_ITEM_FRONT_MATTER_INVALID: {spec_path}")
        try:
            meta = yaml.safe_load(parts[1].strip()) or {}
        except Exception as exc:
            raise ValueError(f"WORK_ITEM_FRONT_MATTER_INVALID: {spec_path}: {exc}") from exc
        if not isinstance(meta, dict):
            raise ValueError(f"WORK_ITEM_FRONT_MATTER_INVALID: {spec_path}: expected mapping")
    else:
        meta = {}
    raw_nested = meta.get("work_item")
    if raw_nested is not None and not isinstance(raw_nested, dict):
        raise ValueError(
            f"WORK_ITEM_METADATA_INVALID: expected mapping, got {type(raw_nested).__name__}"
        )
    nested = raw_nested or {}
    production_evidence = from_spec_metadata(meta, string_value=_metadata_string)
    parent_values = {
        value
        for value in (
            _metadata_string(meta.get("work_item_parent_id"), "WORK_ITEM_PARENT"),
            _metadata_string(meta.get("parent_work_item_id"), "WORK_ITEM_PARENT"),
            _metadata_string(nested.get("parent_id"), "WORK_ITEM_PARENT"),
        )
        if value
    }
    if len(parent_values) > 1:
        raise ValueError(f"WORK_ITEM_PARENT_ALIAS_CONFLICT: values={sorted(parent_values)}")
    parent_id = next(iter(parent_values), "")
    type_values = {
        value.lower()
        for value in (
            _metadata_string(meta.get("work_item_type"), "WORK_ITEM_TYPE"),
            _metadata_string(nested.get("type"), "WORK_ITEM_TYPE"),
        )
        if value
    }
    if len(type_values) > 1:
        raise ValueError(f"WORK_ITEM_TYPE_ALIAS_CONFLICT: values={sorted(type_values)}")
    item_type = next(iter(type_values), "")
    level = str(meta.get("spec_level") or meta.get("bmad_level") or "").strip().upper()
    alternate_parent = alternate_parent_id.strip()
    if alternate_parent and parent_id and alternate_parent != parent_id:
        raise ValueError(
            f"WORK_ITEM_PARENT_CONFLICT: cli={alternate_parent}; spec={parent_id}; source={spec_path}"
        )
    effective_parent = alternate_parent or parent_id
    if item_type and item_type not in {"epic", "story", "task"}:
        raise ValueError(f"WORK_ITEM_TYPE_INVALID: {item_type}; expected epic/story/task")
    if require_l3_type and level == "L3" and not item_type:
        raise ValueError("WORK_ITEM_TYPE_REQUIRED: L3 sync must declare work_item_type=epic|story|task")
    if item_type in {"story", "task"} and not effective_parent:
        raise ValueError(
            f"WORK_ITEM_PARENT_REQUIRED: work_item_type={item_type} requires "
            "work_item_parent_id or --parent-id"
        )
    if item_type == "epic" and effective_parent:
        raise ValueError("WORK_ITEM_PARENT_FORBIDDEN: work_item_type=epic must be top-level")
    expected_parent: str | None = effective_parent or ("" if item_type == "epic" else None)
    return {
        "level": level,
        "item_type": item_type,
        "parent_id": parent_id,
        "effective_parent_id": effective_parent,
        "expected_parent_id": expected_parent,
        "production_evidence": production_evidence,
    }


def resolve_parent_work_item_id(
    spec_path: Path,
    explicit_parent_id: str = "",
    require_l3_type: bool = False,
) -> str:
    contract = work_item_contract_from_spec(
        spec_path,
        require_l3_type=require_l3_type,
        alternate_parent_id=explicit_parent_id,
    )
    return str(contract["effective_parent_id"] or "")


def work_item_drafts_from_spec(
    spec_path: Path,
    assignee: str = "",
    parent_work_item_id: str = "",
    require_l3_type: bool = False,
) -> list[dict[str, Any]]:
    text = spec_path.read_text(encoding="utf-8")
    product_root = product_root_for(spec_path)
    spec_rel = rel_to_product(spec_path, product_root)
    drafts: list[dict[str, Any]] = []
    parent_id = resolve_parent_work_item_id(
        spec_path,
        parent_work_item_id,
        require_l3_type=require_l3_type,
    )
    for index, match in enumerate(ACCEPTANCE_ITEM_RE.finditer(text), start=1):
        title = match.group(1).strip()
        drafts.append(
            {
                "draft_id": f"D{index:03d}",
                "title": title,
                "assignee": assignee,
                "source": spec_rel,
                "contract": "bmad-work-item-v1",
                "parent_work_item_id": parent_id,
                "note": build_work_item_note(spec_path, title, assignee),
            }
        )
    return drafts


def apply_assignee(provider: WorkItemProvider, assignee: str) -> None:
    if assignee and hasattr(provider, "assignee_id"):
        setattr(provider, "assignee_id", assignee)


def sync_spec_markdown(
    provider: WorkItemProvider,
    spec_path: Path,
    assignee: str = "",
    parent_work_item_id: str = "",
) -> tuple[int, str]:
    text = spec_path.read_text(encoding="utf-8")
    count = 0
    contract = work_item_contract_from_spec(
        spec_path,
        require_l3_type=True,
        alternate_parent_id=parent_work_item_id,
    )
    parent_id = str(contract["effective_parent_id"] or "")
    expected_parent = contract["expected_parent_id"]
    expected_project = provider_expected_project_id(provider)
    if (expected_project or expected_parent is not None) and (
        type(provider).verify_binding is WorkItemProvider.verify_binding
    ):
        raise ValueError(f"{provider.name.upper()}_BINDING_VERIFY_UNSUPPORTED")

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        title = match.group(1).strip()
        note = build_work_item_note(spec_path, title, assignee)
        item = (
            provider.create_subtask(parent_id, title=title, note=note)
            if parent_id
            else provider.create(title=title, note=note)
        )
        ok, reason = provider.verify_binding(
            item.id,
            expected_project_id=expected_project,
            expected_parent_id=expected_parent,
        )
        if not ok:
            raise RuntimeError(
                f"WORK_ITEM_BINDING_VERIFY_FAILED: created_id={item.id}; {reason}"
            )
        count += 1
        return f"- [ ] {title} #{item.id}"

    while True:
        match = ACCEPTANCE_ITEM_RE.search(text)
        if not match:
            break
        replacement = repl(match)
        new_text = text[:match.start()] + replacement + text[match.end():]
        created_id = replacement.rsplit("#", 1)[-1]
        try:
            spec_path.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"WORK_ITEM_CREATED_SPEC_WRITE_FAILED: created_id={created_id}; spec={spec_path}; error={exc}"
            ) from exc
        text = new_text
    parent_detail = f"; parent={parent_id}" if parent_id else ""
    return count, f"synced {count} items with bmad-work-item-v1 contract{parent_detail}"


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
        expected_parent: str | None = None
        product_root = active_product_root(harness_root)
        if not spec:
            failures.append("NO_PRODUCT_SPEC_FOR_WORK_ITEM_BINDING")
        elif not product_root:
            failures.append("NO_PRODUCT_ROOT_FOR_WORK_ITEM_BINDING")
        else:
            spec_path = product_root / spec
            try:
                contract = work_item_contract_from_spec(
                    spec_path,
                    require_l3_type=True,
                )
                gate_level = level.strip().upper()
                if gate_level in {"L2", "L3"} and contract["level"] != gate_level:
                    failures.append(
                        "WORK_ITEM_SPEC_LEVEL_MISMATCH: "
                        f"gate={gate_level}; spec={contract['level'] or '<missing>'}; source={spec_path}"
                    )
                expected_parent = contract["expected_parent_id"]
            except ValueError as exc:
                failures.append(str(exc))
        if failures:
            return {"ok": False, "failures": failures, "work_item": None, "provider": provider.name}
        ok, reason = provider.verify_binding(
            wi_id,
            expected_project_id=provider_expected_project_id(provider),
            expected_parent_id=expected_parent,
        )
        if not ok:
            failures.append(reason)
        else:
            work_item = {
                "provider": provider.name,
                "id": wi_id,
                "product_spec": spec,
                "verified": ok,
                "verify_reason": reason,
                "expected_parent_id": expected_parent,
            }
            if contract["production_evidence"]:
                work_item["production_evidence"] = contract["production_evidence"]
    return {"ok": not failures, "failures": failures, "work_item": work_item, "provider": provider.name}
