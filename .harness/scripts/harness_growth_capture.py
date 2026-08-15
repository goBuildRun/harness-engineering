#!/usr/bin/env python3
"""Sanitized evidence capture for Harness growth candidates."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from workspace_paths import Phase0Layout


SECRET_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s`]+"), r"\1<REDACTED>"),
    (
        re.compile(
            r"(?i)\b((?:tenant_access_token|user_access_token|app_access_token|refresh_token|access_token|app_secret|client_secret|FEISHU_APP_SECRET)\s*[:=]\s*)[^\s,;`]+"
        ),
        r"\1<REDACTED>",
    ),
    (re.compile(r"\b[ut]-[A-Za-z0-9_-]{20,}\b"), "<REDACTED_FEISHU_TOKEN>"),
)


def slugify(text: str, fallback: str = "growth-capture") -> str:
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "-", text.strip().lower()).strip("-")
    if raw:
        return raw[:64]
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10] if text else fallback
    return digest[:64]


def sanitize_capture_text(text: str) -> str:
    sanitized = str(text or "")
    for pattern, replacement in SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return re.sub(r"\s+", " ", sanitized).strip()


def code_span(text: str) -> str:
    return sanitize_capture_text(text).replace("`", "'")


def capture_evidence(
    layout: Phase0Layout,
    title: str,
    summary: str,
    category: str = "lesson",
    trigger: str = "",
    failed: str = "",
    cause: str = "",
    next_action: str = "",
    source: str = "",
    command: str = "",
    work_item_id: str = "",
) -> Path:
    created_at = datetime.now(timezone.utc)
    safe_title = slugify(sanitize_capture_text(title) or sanitize_capture_text(summary) or "growth-capture")
    prefix = f"{work_item_id}-" if work_item_id else ""
    out = layout.progress_dir / (
        f"{created_at.strftime('%Y-%m-%dT%H%M%SZ')}-{prefix}{safe_title}-GROWTH-CAPTURE.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    clean_title = sanitize_capture_text(title) or safe_title
    clean_summary = sanitize_capture_text(summary)
    clean_category = sanitize_capture_text(category) or "lesson"
    clean_trigger = sanitize_capture_text(trigger)
    clean_failed = sanitize_capture_text(failed)
    clean_cause = sanitize_capture_text(cause)
    clean_next_action = sanitize_capture_text(next_action)
    clean_source = sanitize_capture_text(source) or "agent-observed"
    clean_command = code_span(command)
    lines = [
        "# GROWTH CAPTURE — 自我成长候选证据",
        "",
        f"- **生成时间**：{created_at.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- **产品**：{layout.product_name}",
        f"- **标题**：{clean_title}",
        f"- **建议分类**：{clean_category}",
        f"- **来源**：{clean_source}",
        *([f"- **Work Item**：`{work_item_id}`"] if work_item_id else []),
        "",
        "## 经验沉淀候选",
        "",
        f"- **原文摘要**：{clean_summary}",
        *([f"- **触发条件**：{clean_trigger}"] if clean_trigger else []),
        *([f"- **失败方案**：{clean_failed}"] if clean_failed else []),
        *([f"- **失败原因**：{clean_cause}"] if clean_cause else []),
        *([f"- **下次正确做法**：{clean_next_action}"] if clean_next_action else []),
        *([f"- **经验沉淀相关命令/入口**：`{clean_command}`"] if clean_command else []),
        "",
        "## Review 建议",
        "",
        "- **人工决定**：待定",
        "- **处理结果**：待处理",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
