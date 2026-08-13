#!/usr/bin/env python3
"""Shared profile business path helpers for Harness gates."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from harness_output import dump_json
from product_context import ProductContextError, resolve_product_context

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

DEFAULT_BUSINESS_ROOTS = (
    "src/",
    "app/",
    "packages/",
    "services/",
    "backend/",
    "frontend/",
    "web/",
    "tests/",
)

def normalize_path(path: str) -> str:
    return path.strip().strip("`").replace("\\", "/").lstrip("./")


def normalize_root(root: str) -> str:
    root = normalize_path(root)
    return root if root.endswith("/") else f"{root}/"


PRODUCT_CONFIG_CANDIDATES = (
    "harness-workspace/project.yaml",
    "harness-workspace/config.yaml",
    ".harness-engineering.yaml",
)


def _load_yaml_file(path: Path) -> dict:
    if yaml is None or not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}


def _profile_from_product_root(product_root: Path) -> str:
    for rel in PRODUCT_CONFIG_CANDIDATES:
        data = _load_yaml_file(product_root / rel)
        product = data.get("product") or {}
        harness = data.get("harness") or {}
        profile = product.get("profile") or harness.get("profile")
        if profile:
            return str(profile)
    return ""


def _product_config_from_root(product_root: Path | None) -> dict:
    if product_root is None:
        return {}
    for rel in PRODUCT_CONFIG_CANDIDATES:
        data = _load_yaml_file(product_root / rel)
        if data:
            return data
    return {}


def _active_product_root(harness_root: Path) -> Path | None:
    try:
        return resolve_product_context(harness_root, include_legacy=False).root
    except ProductContextError:
        return None


def resolve_profile(harness_root: Path, product_root: Path | None = None, explicit_profile: str = "") -> str:
    if explicit_profile:
        return explicit_profile
    if product_root:
        profile = _profile_from_product_root(product_root)
        if profile:
            return profile
    active_root = _active_product_root(harness_root)
    if active_root:
        profile = _profile_from_product_root(active_root)
        if profile:
            return profile
    harness_cfg = _load_yaml_file(harness_root / ".harness" / "config.yaml")
    harness = harness_cfg.get("harness") or {}
    return str(harness.get("profile") or "generic")


def allowlist_path(harness_root: Path, profile: str = "", product_root: Path | None = None) -> Path:
    selected_profile = resolve_profile(harness_root, product_root, profile)
    profile_path = harness_root / ".harness" / "profiles" / selected_profile / "package-allowlist.yaml"
    if profile_path.is_file():
        return profile_path
    if selected_profile == "generic":
        return harness_root / ".harness" / "rules" / "package-allowlist.yaml"
    return profile_path


def _platform_business_roots(product_config: dict) -> tuple[str, ...]:
    platform = product_config.get("platform_product")
    if not isinstance(platform, dict) or not platform:
        return ()
    roots = platform.get("source_roots") or {}
    platform_roots: list[str] = []
    if isinstance(roots, dict):
        platform_roots.extend(normalize_root(str(root)) for root in roots.values() if str(root).strip())

    workspace = product_config.get("workspace") or {}
    workspace_root = workspace.get("root")
    if workspace_root:
        platform_roots.append(normalize_root(str(workspace_root)))

    return tuple(dict.fromkeys(platform_roots))


def load_business_roots(harness_root: Path, profile: str = "", product_root: Path | None = None) -> tuple[str, ...]:
    selected_product_root = product_root or _active_product_root(harness_root)
    product_config = _product_config_from_root(selected_product_root)
    platform_roots = _platform_business_roots(product_config)
    if platform_roots:
        return tuple(dict.fromkeys(platform_roots))

    rules_path = allowlist_path(harness_root, profile, product_root)
    if yaml is None or not rules_path.is_file():
        return DEFAULT_BUSINESS_ROOTS

    try:
        data = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    except OSError:
        return DEFAULT_BUSINESS_ROOTS

    roots = [
        normalize_root(str(root))
        for root in data.get("allowed_roots") or []
        if normalize_root(str(root))
    ]
    return tuple(dict.fromkeys(roots or DEFAULT_BUSINESS_ROOTS))


def is_business_path(path: str, roots: tuple[str, ...] = DEFAULT_BUSINESS_ROOTS) -> bool:
    p = normalize_path(path)
    return any(p == root.rstrip("/") or p.startswith(root) for root in roots)


def _path_pattern(roots: tuple[str, ...]) -> re.Pattern[str]:
    alternates = "|".join(
        rf"{re.escape(root.rstrip('/'))}(?:/[^\s`'\"|,\];；，。)]*)?"
        for root in sorted(roots, key=len, reverse=True)
    )
    return re.compile(rf"(?<![A-Za-z0-9_./-])(?:{alternates})(?![A-Za-z0-9_.-])")


def find_business_paths(text: str, roots: tuple[str, ...] = DEFAULT_BUSINESS_ROOTS) -> list[str]:
    paths: dict[str, None] = {}
    for match in _path_pattern(roots).finditer(text):
        path = normalize_path(match.group(0).rstrip("`'\"),;，；。]}>"))
        if is_business_path(path, roots):
            paths[path] = None
    return list(paths)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=("roots", "extract", "contains", "json"))
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--profile", default="")
    args = parser.parse_args()

    product_root = Path(args.product_root).resolve() if args.product_root else _active_product_root(Path(args.harness_root).resolve())
    roots = load_business_roots(Path(args.harness_root).resolve(), args.profile, product_root)

    if args.cmd == "roots":
        print("\n".join(roots))
        return 0
    if args.cmd == "json":
        dump_json(list(roots))
        return 0

    text = sys.stdin.read()
    paths = find_business_paths(text, roots)
    if args.cmd == "contains":
        return 0 if paths else 1
    print("\n".join(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
