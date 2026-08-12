#!/usr/bin/env python3
"""Read-only local product registry and active-product path helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def products_dir(harness_root: Path) -> Path:
    return harness_root / ".harness" / "products"


def registry_path(harness_root: Path) -> Path:
    return products_dir(harness_root) / "registry.yaml"


def active_path(harness_root: Path) -> Path:
    return products_dir(harness_root) / "active-product.json"


def load_registry(harness_root: Path) -> dict[str, Any]:
    path = registry_path(harness_root)
    if yaml is None or not path.is_file():
        return {"products": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    products = data.get("products")
    if not isinstance(products, list):
        data["products"] = []
    return data


def _safe_existing_dir(value: str) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    try:
        return path.resolve() if path.is_dir() else None
    except (OSError, RuntimeError):
        return None


def _safe_resolve(value: str) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def _registry_products(harness_root: Path) -> list[dict[str, Any]]:
    return [p for p in (load_registry(harness_root).get("products") or []) if isinstance(p, dict)]


def find_registered_by_id(harness_root: Path, product_id: str) -> dict[str, Any] | None:
    wanted = str(product_id or "").strip()
    if not wanted:
        return None
    return next((p for p in _registry_products(harness_root) if str(p.get("id") or "") == wanted), None)


def find_registered_by_root(harness_root: Path, product_root: Path) -> dict[str, Any] | None:
    wanted = product_root.resolve()
    for product in _registry_products(harness_root):
        registered_root = _safe_resolve(str(product.get("root") or ""))
        if registered_root == wanted:
            return product
    return None
