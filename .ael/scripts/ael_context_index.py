#!/usr/bin/env python3
"""Digest/index based product knowledge context without copying full artifacts."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ael_runtime import now
from workspace_paths import Phase0Layout


SCHEMA = "harness-context-index-v1"
SUMMARY_LIMIT = 800


def _summary(text: str) -> str:
    lines = []
    in_managed_table = False
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw.strip())
        if not line or line.startswith("<!--"):
            continue
        if line.startswith("|"):
            if in_managed_table:
                continue
            in_managed_table = True
        elif not line.startswith(("#", ">")):
            in_managed_table = False
        lines.append(line)
        if sum(len(item) for item in lines) >= SUMMARY_LIMIT:
            break
    value = "\n".join(lines)
    return value[:SUMMARY_LIMIT].rstrip()


def build_index(layout: Phase0Layout) -> dict[str, Any]:
    artifacts = []
    for kind, path in (
        ("context", layout.context_file),
        ("lessons", layout.lessons_file),
        ("reference_systems", layout.reference_systems_file),
    ):
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            raw = ""
        artifacts.append({
            "kind": kind,
            "ref": layout.rel(path),
            "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else "absent",
            "chars": len(raw),
            "summary": _summary(raw) if raw else "",
        })
    return {
        "schema": SCHEMA,
        "generated_at": now(),
        "product_id": layout.product_id,
        "artifacts": artifacts,
        "full_text_included": False,
    }


def write_index(layout: Phase0Layout, output: Path) -> dict[str, Any]:
    payload = build_index(layout)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def render_index(payload: dict[str, Any]) -> str:
    lines = ["## 产品知识注入（摘要索引）", ""]
    for item in payload.get("artifacts") or []:
        lines.extend([
            f"### {item['kind']}",
            f"- Ref: `{item['ref']}`",
            f"- SHA-256: `{item['sha256']}`",
            f"- Chars: {item['chars']}",
            "",
            str(item.get("summary") or "（空）"),
            "",
        ])
    lines.append("完整正文按需读取上述 Ref；默认上下文只携带摘要和 digest。")
    return "\n".join(lines)
