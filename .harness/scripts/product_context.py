#!/usr/bin/env python3
"""Shared product context resolver for harness-engineering scripts."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from harness_output import dump_json
from product_registry import (
    _registry_products,
    _safe_existing_dir,
    active_path,
    find_registered_by_id,
    find_registered_by_root,
    load_registry,
    products_dir,
    registry_path,
)

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


LEGACY_PRODUCT_ROOT_VAR = "PRODUCT_ROOT"

PRODUCT_CONFIG_CANDIDATES = (
    "harness-workspace/project.yaml",
    "harness-workspace/config.yaml",
    ".harness-engineering.yaml",
)


class ProductContextError(RuntimeError):
    """Raised when an explicit product selector is invalid."""


@dataclass(frozen=True)
class ProductContext:
    root: Path
    source: str
    product_id: str = ""
    workspace: str = "harness-workspace"
    config: str = "harness-workspace/project.yaml"
    product: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "root": str(self.root),
            "workspace": self.workspace,
            "config": self.config,
            "source": self.source,
            "registered": bool(self.product),
            "product": self.product,
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None or not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def product_config(root: Path) -> dict[str, Any]:
    for rel in PRODUCT_CONFIG_CANDIDATES:
        data = _load_yaml(root / rel)
        if data:
            return data
    return {}


def product_id_from_config(root: Path) -> str:
    data = product_config(root)
    product = data.get("product") or {}
    harness = data.get("harness") or {}
    return str(product.get("id") or harness.get("product_id") or "").strip()


def _workspace_from_config(root: Path) -> str:
    data = product_config(root)
    workspace = data.get("workspace") or {}
    value = workspace.get("root") if isinstance(workspace, dict) else ""
    return str(value or "harness-workspace").strip("/") or "harness-workspace"


def _context_from_root(
    harness_root: Path,
    root: Path,
    source: str,
    product_id: str = "",
    registered: dict[str, Any] | None = None,
) -> ProductContext:
    product = dict(registered or find_registered_by_root(harness_root, root) or {})
    workspace = str(product.get("workspace") or _workspace_from_config(root) or "harness-workspace").strip("/")
    config = str(product.get("config") or f"{workspace}/project.yaml")
    resolved_id = str(product_id or product.get("id") or product_id_from_config(root) or "").strip()
    return ProductContext(
        root=root.resolve(),
        source=source,
        product_id=resolved_id,
        workspace=workspace,
        config=config,
        product=product,
    )


def _context_from_registered(harness_root: Path, product: dict[str, Any], source: str, strict: bool) -> ProductContext | None:
    root = _safe_existing_dir(str(product.get("root") or ""))
    if root is None:
        if strict:
            raise ProductContextError(f"PRODUCT_ROOT_NOT_FOUND: product_id={product.get('id') or ''}")
        return None
    return _context_from_root(harness_root, root, source, product_id=str(product.get("id") or ""), registered=product)


def _resolve_selector(
    harness_root: Path,
    product_root: str = "",
    product_id: str = "",
    source: str = "selector",
    strict: bool = True,
) -> ProductContext | None:
    product_root = str(product_root or "").strip()
    product_id = str(product_id or "").strip()
    if not product_root and not product_id:
        return None

    if product_root:
        root = _safe_existing_dir(product_root)
        if root is None:
            if strict:
                raise ProductContextError(f"PRODUCT_ROOT_NOT_FOUND: {product_root}")
            return None

        registered_by_root = find_registered_by_root(harness_root, root)
        if product_id:
            registered_by_id = find_registered_by_id(harness_root, product_id)
            config_id = product_id_from_config(root)
            if registered_by_id:
                registered_id_root = _safe_existing_dir(str(registered_by_id.get("root") or ""))
                if registered_id_root and registered_id_root != root:
                    raise ProductContextError(
                        f"PRODUCT_SELECTOR_CONFLICT: product_id={product_id} root={root} registry_root={registered_id_root}"
                    )
                registered_by_root = registered_by_id
            elif config_id and config_id != product_id:
                raise ProductContextError(
                    f"PRODUCT_SELECTOR_CONFLICT: product_id={product_id} project_yaml_id={config_id}"
                )
        return _context_from_root(harness_root, root, source, product_id=product_id, registered=registered_by_root)

    registered = find_registered_by_id(harness_root, product_id)
    if registered is None:
        if strict:
            raise ProductContextError(f"PRODUCT_NOT_REGISTERED: product_id={product_id}")
        return None
    return _context_from_registered(harness_root, registered, source, strict=strict)


def _env_selector(environ: Mapping[str, str]) -> tuple[str, str]:
    root = (
        environ.get("HARNESS_PRODUCT_ROOT")
        or environ.get(LEGACY_PRODUCT_ROOT_VAR)
        or ""
    )
    product_id = environ.get("HARNESS_PRODUCT_ID") or ""
    return root, product_id


def _cwd_context(harness_root: Path, cwd: Path) -> ProductContext | None:
    current = cwd.resolve()
    registered: list[tuple[int, dict[str, Any], Path]] = []
    for product in _registry_products(harness_root):
        root = _safe_existing_dir(str(product.get("root") or ""))
        if root is None:
            continue
        try:
            current.relative_to(root)
            registered.append((len(root.parts), product, root))
        except ValueError:
            continue
    if registered:
        _, product, root = sorted(registered, key=lambda item: item[0], reverse=True)[0]
        return _context_from_root(harness_root, root, "cwd:registry", product_id=str(product.get("id") or ""), registered=product)

    for candidate in (current, *current.parents):
        if any((candidate / rel).is_file() for rel in PRODUCT_CONFIG_CANDIDATES):
            return _context_from_root(harness_root, candidate.resolve(), "cwd:project-config")
    return None


def _active_context(harness_root: Path) -> ProductContext | None:
    path = active_path(harness_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return _resolve_selector(
        harness_root,
        product_root=str(data.get("root") or ""),
        product_id=str(data.get("product_id") or ""),
        source="active-product",
        strict=False,
    )


def _marker_context(harness_root: Path) -> ProductContext | None:
    for marker in (".product-root",):
        path = harness_root / marker
        if not path.is_file():
            continue
        value = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0].strip()
        if not value:
            continue
        target = Path(value).expanduser() if Path(value).is_absolute() else (harness_root / value)
        root = _safe_existing_dir(str(target))
        if root is not None:
            return _context_from_root(harness_root, root, f"marker:{marker}")
    return None


def _legacy_search_context(harness_root: Path) -> ProductContext | None:
    search = harness_root.resolve()
    for _ in range(4):
        if (
            (search / "services").is_dir()
            or (search / "src").is_dir()
            or (search / "package.json").is_file()
            or (search / "pyproject.toml").is_file()
            or (search / "architecture").is_dir()
        ):
            return _context_from_root(harness_root, search, "legacy-search")
        if search.parent == search:
            break
        search = search.parent
    parent = harness_root.parent.resolve()
    return _context_from_root(harness_root, parent, "legacy-parent")


def resolve_product_context(
    harness_root: Path,
    product_root: str = "",
    product_id: str = "",
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
    include_cwd: bool = True,
    include_active: bool = True,
    include_legacy: bool = True,
) -> ProductContext:
    harness_root = harness_root.resolve()

    explicit = _resolve_selector(harness_root, product_root=product_root, product_id=product_id, source="argument", strict=True)
    if explicit:
        return explicit

    env_root, env_product_id = _env_selector(environ or os.environ)
    env_context = _resolve_selector(
        harness_root,
        product_root=env_root,
        product_id=env_product_id,
        source="environment",
        strict=True,
    )
    if env_context:
        return env_context

    if include_cwd:
        cwd_context = _cwd_context(harness_root, cwd or Path.cwd())
        if cwd_context:
            return cwd_context

    if include_active:
        active = _active_context(harness_root)
        if active:
            return active

    if include_legacy:
        marker = _marker_context(harness_root)
        if marker:
            return marker
        legacy = _legacy_search_context(harness_root)
        if legacy:
            return legacy

    raise ProductContextError("PRODUCT_CONTEXT_NOT_FOUND")


def resolve_product_root(harness_root: Path, **kwargs: Any) -> Path:
    return resolve_product_context(harness_root, **kwargs).root


def _harness_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_path(value: str) -> Path | None:
    return Path(value).expanduser().resolve() if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve harness product context")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--harness-root", default="")
        p.add_argument("--product-root", default="")
        p.add_argument("--product-id", default="")
        p.add_argument("--cwd", default="")
        p.add_argument("--no-active", action="store_true")
        p.add_argument("--no-legacy", action="store_true")

    p_root = sub.add_parser("resolve-root")
    add_common(p_root)

    p_json = sub.add_parser("resolve")
    add_common(p_json)

    args = parser.parse_args()
    harness_root = Path(args.harness_root).expanduser().resolve() if args.harness_root else _harness_root_from_script()
    try:
        context = resolve_product_context(
            harness_root,
            product_root=args.product_root,
            product_id=args.product_id,
            cwd=_parse_path(args.cwd),
            include_active=not args.no_active,
            include_legacy=not args.no_legacy,
        )
    except ProductContextError as exc:
        if args.cmd == "resolve":
            dump_json({"decision": "block", "reason": f"PRODUCT_CONTEXT_ERROR: {exc}"})
        else:
            print(f"PRODUCT_CONTEXT_ERROR: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "resolve-root":
        print(context.root)
        return 0
    dump_json({"decision": "pass", "reason": "PRODUCT_CONTEXT_RESOLVED", "product_context": context.to_dict()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
