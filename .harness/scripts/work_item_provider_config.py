#!/usr/bin/env python3
"""Configuration loading for Work Item provider adapters."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from product_context import ProductContextError, resolve_product_context

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


PRODUCT_CONFIG_CANDIDATES = (
    "harness-workspace/project.yaml",
    "harness-workspace/config.yaml",
    ".harness-engineering.yaml",
)
DEFAULT_HARNESS_NAME = "Team Product R&D Harness"
GENERIC_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"


def load_dotenv(harness_root: Path) -> None:
    env_path = harness_root / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


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
        explicit_vars = ("HARNESS_PRODUCT_ROOT", "HARNESS_PRODUCT_ID")
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
    normalized = {
        key: work_item[key]
        for key in ("provider", "id_pattern", "requirements")
        if key in work_item
    }
    providers = dict(work_item.get("providers") or {})
    for name in ("noop", "teambition", "feishu", "jira"):
        if isinstance(work_item.get(name), dict):
            providers[name] = deep_merge(providers.get(name) or {}, work_item[name])
    if providers:
        normalized["providers"] = providers
    return normalized


def load_config(harness_root: Path) -> dict[str, Any]:
    load_dotenv(harness_root)
    cfg: dict[str, Any] = _load_yaml(harness_root / ".harness/work-items/config.yaml")
    product_cfg = normalize_product_work_item_config(load_product_work_item_config(harness_root))
    if product_cfg:
        cfg = deep_merge(cfg, product_cfg)
    cfg["provider"] = os.environ.get("WORK_ITEM_PROVIDER", cfg.get("provider", "noop"))
    cfg.setdefault("id_pattern", GENERIC_ID_PATTERN)
    cfg.setdefault("requirements", {"L1": False, "L2": True, "L3": True})
    return cfg


def load_harness_name(harness_root: Path) -> str:
    product_root = active_product_root(harness_root)
    if product_root is not None:
        for rel in PRODUCT_CONFIG_CANDIDATES:
            data = _load_yaml(product_root / rel)
            name = str((data.get("harness") or {}).get("name") or "").strip()
            if name:
                return name
    data = _load_yaml(harness_root / ".harness/config.yaml")
    name = str((data.get("harness") or {}).get("name") or DEFAULT_HARNESS_NAME).strip()
    return name or DEFAULT_HARNESS_NAME
