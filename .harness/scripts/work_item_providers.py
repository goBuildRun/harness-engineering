#!/usr/bin/env python3
"""work_item_providers.py — pluggable Work Item adapters."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from product_context import ProductContextError, resolve_product_context

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


@dataclass
class WorkItem:
    id: str
    title: str
    status: str = "unknown"
    note: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    url: str | None = None
    provider: str = "noop"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def load_dotenv(harness_root: Path) -> None:
    """加载 harness 根目录 .env（不覆盖已有环境变量）。"""
    env_path = harness_root / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key and key not in os.environ:
            os.environ[key] = val


PRODUCT_CONFIG_CANDIDATES = (
    "harness-workspace/project.yaml",
    "harness-workspace/config.yaml",
    ".harness-engineering.yaml",
)

DEFAULT_HARNESS_NAME = "Team Product R&D Harness"
GENERIC_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"
ACCEPTANCE_ITEM_RE = re.compile(
    r"^- \[ \] (?!.*#[A-Za-z0-9][A-Za-z0-9._:-]{1,127}\b)(.+)$",
    re.MULTILINE,
)


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None or not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}


def active_product_root(harness_root: Path) -> Path | None:
    try:
        return resolve_product_context(harness_root, include_legacy=False).root
    except ProductContextError:
        explicit_vars = (
            "HARNESS_PRODUCT_ROOT",
            "HARNESS_PRODUCT_ID",
            "HARNESS_PRODUCT_ROOT",
        )
        if any(os.environ.get(var) for var in explicit_vars):
            raise
        return None


def load_product_work_item_config(harness_root: Path) -> dict[str, Any]:
    root = active_product_root(harness_root)
    if root is None:
        return {}
    for rel in PRODUCT_CONFIG_CANDIDATES:
        data = _load_yaml(root / rel)
        work_item = data.get("work_item") or data.get("workItem") or {}
        if work_item:
            return work_item
    return {}


def normalize_product_work_item_config(work_item: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in ("provider", "id_pattern", "requirements"):
        if key in work_item:
            normalized[key] = work_item[key]
    providers = dict(work_item.get("providers") or {})
    for name in ("noop", "teambition", "feishu", "jira"):
        if isinstance(work_item.get(name), dict):
            providers[name] = deep_merge(providers.get(name) or {}, work_item[name])
    if providers:
        normalized["providers"] = providers
    return normalized


def load_config(harness_root: Path) -> dict[str, Any]:
    load_dotenv(harness_root)
    cfg_path = harness_root / ".harness/work-items/config.yaml"
    cfg: dict[str, Any] = _load_yaml(cfg_path)
    product_cfg = normalize_product_work_item_config(load_product_work_item_config(harness_root))
    if product_cfg:
        cfg = deep_merge(cfg, product_cfg)
    provider = os.environ.get("WORK_ITEM_PROVIDER", cfg.get("provider", "noop"))
    cfg["provider"] = provider
    cfg.setdefault("id_pattern", GENERIC_ID_PATTERN)
    cfg.setdefault("requirements", {"L1": False, "L2": True, "L3": True})
    return cfg


def load_harness_name(harness_root: Path) -> str:
    product_root = active_product_root(harness_root)
    if product_root is not None:
        for rel in PRODUCT_CONFIG_CANDIDATES:
            data = _load_yaml(product_root / rel)
            harness = data.get("harness") or {}
            name = str(harness.get("name") or "").strip()
            if name:
                return name
    data = _load_yaml(harness_root / ".harness/config.yaml")
    harness = data.get("harness") or {}
    return str(harness.get("name") or DEFAULT_HARNESS_NAME).strip() or DEFAULT_HARNESS_NAME


def valid_id(work_item_id: str, pattern: str) -> bool:
    return bool(re.fullmatch(pattern, work_item_id.strip()))


def extract_id_from_text(text: str) -> str | None:
    patterns = [
        r"任务编号[：:]\s*`?([^`\s，,；;]+)",
        r"Work\s*Item(?:\s*ID)?[：:]\s*`?([^`\s，,；;]+)",
        r"work_item_id[：:]\s*`?([^`\s，,；;]+)",
        r"#([A-Za-z0-9][A-Za-z0-9._:-]{1,127})\b",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if not m:
            continue
        candidate = m.group(1).strip().strip("`")
        if candidate in {"N/A", "NA", "无", "待填", "待确认"} or "<" in candidate or ">" in candidate:
            continue
        return candidate
    return None


def extract_from_task_dir(task_dir: Path) -> tuple[str | None, str | None]:
    """返回 (work_item_id, product_spec_rel_path)。"""
    wi_id: str | None = None
    spec: str | None = None
    card = task_dir / "00-任务卡.md"
    if card.is_file():
        text = card.read_text(encoding="utf-8", errors="ignore")
        wi_id = extract_id_from_text(text)
        m = re.search(r"产品规格[链接]*[：:]\s*`?([^`\s]+product-specs/[^`\s]+)`?", text)
        if m:
            spec = m.group(1).strip()
    return wi_id, spec


class WorkItemProvider(ABC):
    name: str

    @abstractmethod
    def verify(self, work_item_id: str) -> tuple[bool, str]:
        ...

    @abstractmethod
    def pull(self, work_item_id: str) -> WorkItem:
        ...

    @abstractmethod
    def create(self, title: str, note: str = "", project_id: str | None = None) -> WorkItem:
        ...

    @abstractmethod
    def update_status(self, work_item_id: str, status: str, note: str = "") -> tuple[bool, str]:
        ...

    def update_description(self, work_item_id: str, description: str) -> tuple[bool, str]:
        return False, f"{self.name.upper()}_DESCRIPTION_UPDATE_UNSUPPORTED"

    def update_title(self, work_item_id: str, title: str) -> tuple[bool, str]:
        return False, f"{self.name.upper()}_TITLE_UPDATE_UNSUPPORTED"

    def create_subtask(self, parent_work_item_id: str, title: str, note: str = "") -> WorkItem:
        raise NotImplementedError(f"{self.name.upper()}_SUBTASK_CREATE_UNSUPPORTED")


class NoopProvider(WorkItemProvider):
    name = "noop"

    def __init__(self, id_pattern: str) -> None:
        self.id_pattern = id_pattern

    def verify(self, work_item_id: str) -> tuple[bool, str]:
        if valid_id(work_item_id, self.id_pattern):
            return True, "NOOP_VERIFY: ID 格式合法（未调用外部 API）"
        return False, f"NOOP_INVALID_ID: 须匹配 {self.id_pattern}"

    def pull(self, work_item_id: str) -> WorkItem:
        ok, msg = self.verify(work_item_id)
        if not ok:
            raise ValueError(msg)
        return WorkItem(
            id=work_item_id,
            title=f"本地 Work Item {work_item_id}",
            status="local",
            note="noop provider：从仓库 tasks/ 与 product-specs 读取为主",
            provider=self.name,
        )

    @staticmethod
    def _local_id_from_title(title: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title.strip().lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        slug = slug[:72].strip("-") or "local"
        return f"{slug}-{uuid.uuid4().hex[:8]}"

    def create(self, title: str, note: str = "", project_id: str | None = None) -> WorkItem:
        synthetic = self._local_id_from_title(title)
        return WorkItem(id=synthetic, title=title, note=note, status="local", provider=self.name)

    def update_status(self, work_item_id: str, status: str, note: str = "") -> tuple[bool, str]:
        ok, _ = self.verify(work_item_id)
        if not ok:
            return False, "NOOP_INVALID_ID"
        return True, f"NOOP_STATUS: 本地记录 status={status}"

    def list_assigned(self, assignee_id: str | None = None) -> list[WorkItem]:
        return []


from work_item_feishu import FeishuProvider  # noqa: E402
from work_item_jira import JiraProvider  # noqa: E402
from work_item_teambition import TeambitionProvider  # noqa: E402


REGISTRY: dict[str, type[WorkItemProvider]] = {
    "noop": NoopProvider,
    "teambition": TeambitionProvider,
    "feishu": FeishuProvider,
    "jira": JiraProvider,
}


def get_provider(harness_root: Path) -> WorkItemProvider:
    cfg = load_config(harness_root)
    name = cfg.get("provider", "noop")
    prov_cfg = dict((cfg.get("providers") or {}).get(name) or {})
    product_root = active_product_root(harness_root)
    if product_root:
        prov_cfg.setdefault("_product_root", str(product_root))
    pattern = os.environ.get("WORK_ITEM_ID_PATTERN") or prov_cfg.get("id_pattern") or cfg.get("id_pattern") or GENERIC_ID_PATTERN
    cls = REGISTRY.get(name)
    if not cls:
        raise ValueError(f"UNKNOWN_PROVIDER: {name}")
    if name == "teambition":
        return TeambitionProvider(prov_cfg, pattern)
    if name == "feishu":
        return FeishuProvider(prov_cfg, pattern)
    if name == "jira":
        return JiraProvider(prov_cfg, pattern)
    return NoopProvider(pattern)


from work_item_contract import (  # noqa: E402
    apply_assignee,
    build_work_item_note,
    first_heading,
    gate_check,
    level_requires_work_item,
    parse_front_matter,
    product_root_for,
    rel_to_product,
    section_bullets,
    section_text,
    sync_spec_markdown,
    work_item_drafts_from_spec,
)
