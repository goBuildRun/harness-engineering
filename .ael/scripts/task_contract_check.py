#!/usr/bin/env python3
"""Check implementation-plan task contracts."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from business_paths import find_business_paths, load_business_roots
from ael_output import dump_json
from workspace_paths import active_planning_gate_path, load_layout, resolve_task_dir

FIELD_ALIASES = {
    "id": ("id", "任务id", "任务 id"),
    "name": ("name", "名称", "描述", "任务"),
    "owner": ("owner", "负责agent", "负责人", "负责 agent"),
    "depends_on": ("depends_on", "depends on", "前置依赖", "依赖"),
    "read_files": ("read_files", "read files", "读取边界", "参考文件", "read"),
    "write_files": ("write_files", "write files", "写入边界", "目标路径", "目标路径必填", "write"),
    "action": ("action", "动作", "实施动作", "要做什么"),
    "verify": ("verify", "验证命令", "验证方式", "验收产出"),
    "done": ("done", "完成判定", "完成标准", "验收标准"),
}

REQUIRED = ("id", "read_files", "write_files", "action", "verify", "done")


def emit(decision: str, reason: str, **extra) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def norm(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("**", "").replace("`", "")
    value = re.sub(r"[()\[\]（）【】/·:：_ -]+", "", value)
    return value.strip().lower()


def split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_separator(line: str) -> bool:
    cells = split_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells)


def canonical_headers(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    normalized = [norm(h) for h in headers]
    for field, aliases in FIELD_ALIASES.items():
        normalized_aliases = [norm(alias) for alias in aliases]
        for idx, header in enumerate(normalized):
            if any(alias == header or alias in header for alias in normalized_aliases):
                mapping[field] = idx
                break
    return mapping


def parse_task_rows(text: str) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    issues: list[str] = []
    lines = text.splitlines()
    for i in range(len(lines) - 2):
        if "|" not in lines[i] or not is_separator(lines[i + 1]):
            continue
        headers = split_row(lines[i])
        mapping = canonical_headers(headers)
        if "id" not in mapping:
            continue
        missing_headers = [field for field in REQUIRED if field not in mapping]
        if missing_headers:
            issues.append(f"TASK_TABLE_MISSING_COLUMNS line={i + 1}: {missing_headers}")
            continue
        j = i + 2
        while j < len(lines) and "|" in lines[j] and not is_separator(lines[j]):
            cells = split_row(lines[j])
            if len(cells) < len(headers):
                j += 1
                continue
            row = {field: cells[idx].strip() for field, idx in mapping.items() if idx < len(cells)}
            if row.get("id"):
                rows.append(row)
            j += 1
    return rows, issues


def task_file(args: argparse.Namespace, layout) -> Path:
    if args.plan:
        return Path(args.plan).resolve()
    task_dir = args.task_dir
    if not task_dir:
        gate = active_planning_gate_path(layout)
        if gate.is_file():
            try:
                task_dir = json.loads(gate.read_text(encoding="utf-8")).get("task_dir") or ""
            except (json.JSONDecodeError, OSError):
                task_dir = ""
    if task_dir:
        p = resolve_task_dir(layout, task_dir, cwd=Path.cwd())
        return (p / "03-实施方案.md").resolve()
    return (layout.tasks / "03-实施方案.md").resolve()


def validate(rows: list[dict[str, str]], text: str, business_roots: tuple[str, ...]) -> list[str]:
    issues: list[str] = []
    seen: set[str] = set()
    if not rows:
        issues.append("TASK_CONTRACT_NO_TASKS: 03-实施方案.md 未找到含 7 字段任务契约的表格")
        return issues

    for row in rows:
        tid = row.get("id", "").strip("` ")
        if not re.fullmatch(r"T(?:-FIX-)?[A-Za-z0-9._-]+", tid):
            issues.append(f"TASK_ID_INVALID: {tid}")
        if tid in seen:
            issues.append(f"TASK_ID_DUPLICATE: {tid}")
        seen.add(tid)

        for field in REQUIRED:
            value = row.get(field, "").strip()
            if not value:
                issues.append(f"TASK_FIELD_EMPTY: {tid}.{field}")

        write_paths = find_business_paths(row.get("write_files", ""), business_roots)
        if not write_paths:
            issues.append(f"TASK_WRITE_FILES_NO_BUSINESS_PATH: {tid}")

        verify = row.get("verify", "").strip()
        if verify and not re.search(r"\b(test|pytest|unittest|npm|pnpm|yarn|go test|cargo test|mvn|gradle|structure_guard|check)\b|测试|验证", verify):
            issues.append(f"TASK_VERIFY_NOT_EXECUTABLE_ENOUGH: {tid} verify={verify}")

        done = row.get("done", "").strip()
        if done in {"待补", "TODO", "TBD", "-"}:
            issues.append(f"TASK_DONE_PLACEHOLDER: {tid}")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ael-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--product-id", default="")
    parser.add_argument("--task-dir", default="")
    parser.add_argument("--plan", default="")
    args = parser.parse_args()

    layout = load_layout(
        Path(args.ael_root).resolve(),
        Path(args.product_root).resolve() if (args.product_root) else None,
        args.product_id,
    )
    plan = task_file(args, layout)
    if not plan.is_file():
        emit("block", f"TASK_CONTRACT_PLAN_MISSING: {plan}")
        return 0

    text = plan.read_text(encoding="utf-8", errors="ignore")
    rows, table_issues = parse_task_rows(text)
    business_roots = load_business_roots(layout.ael_root, product_root=layout.product_root)
    issues = table_issues + validate(rows, text, business_roots)
    if issues:
        emit("block", "TASK_CONTRACT_INVALID: " + "; ".join(issues), task_count=len(rows))
        return 0

    emit("pass", f"TASK_CONTRACT_OK: {len(rows)} 个任务满足 7 字段契约", task_count=len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
