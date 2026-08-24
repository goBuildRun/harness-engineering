#!/usr/bin/env python3
"""Initialize and select product workspaces for the buildrun-agent-engineering-lifecycle."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ael_init_bmad import (
    bmad_output_root,
    ensure_bmad_output_config,
    maybe_install_bmad,
    render_bmad_config_block,
)
from ael_init_workspace import WORK_ITEM_ID_PATTERNS, ensure_product_workspace
from ael_output import dump_json
from ael_assurance import configure as configure_assurance

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def emit(decision: str, reason: str, **extra: Any) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def ael_root() -> Path:
    return Path(__file__).resolve().parents[2]
def products_dir() -> Path:
    return ael_root() / ".ael" / "products"
def registry_path() -> Path:
    return products_dir() / "registry.yaml"
def active_path() -> Path:
    return products_dir() / "active-product.json"
def load_registry() -> dict[str, Any]:
    if yaml is None or not registry_path().is_file():
        return {"products": []}
    return yaml.safe_load(registry_path().read_text(encoding="utf-8")) or {"products": []}


def write_registry(data: dict[str, Any]) -> None:
    products_dir().mkdir(parents=True, exist_ok=True)
    if yaml is None:
        raise RuntimeError("PyYAML is required to write product registry")
    registry_path().write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def write_active(product: dict[str, Any]) -> None:
    products_dir().mkdir(parents=True, exist_ok=True)
    active = {
        "product_id": product["id"],
        "root": product["root"],
        "workspace": product.get("workspace") or "ael-workspace",
        "config": product.get("config") or "ael-workspace/project.yaml",
    }
    rendered = json.dumps(active, ensure_ascii=False, indent=2) + "\n"
    path = active_path()
    if path.is_file() and path.read_text(encoding="utf-8") == rendered:
        return
    path.write_text(rendered, encoding="utf-8")


def upsert_product(product: dict[str, Any]) -> dict[str, Any]:
    data = load_registry()
    products = data.setdefault("products", [])
    for idx, item in enumerate(products):
        if item.get("id") == product["id"]:
            products[idx] = {**item, **product}
            write_registry(data)
            return products[idx]
    products.append(product)
    write_registry(data)
    return product


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.product_root).resolve()
    if not root.is_dir():
        emit("block", f"PRODUCT_ROOT_NOT_FOUND: {root}")
        return 0
    if args.workspace != "ael-workspace":
        emit("block", "NON_CANONICAL_WORKSPACE: use ael-workspace", requested_workspace=args.workspace)
        return 0

    registered = next(
        (item for item in load_registry().get("products", []) if item.get("id") == args.product_id),
        {},
    )
    config_provider = ""
    config_pattern = ""
    config_path = root / "ael-workspace" / "project.yaml"
    if yaml is not None and config_path.is_file():
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        work_item = config_data.get("work_item", {}) if isinstance(config_data, dict) else {}
        if isinstance(work_item, dict):
            config_provider = str(work_item.get("provider") or "")
            config_pattern = str(work_item.get("id_pattern") or "")

    registered_provider = str(registered.get("work_item_provider") or "")
    existing_provider = config_provider or registered_provider
    requested_provider = str(args.work_item_provider or "")
    if requested_provider and existing_provider and requested_provider != existing_provider and not args.overwrite_config:
        emit(
            "block",
            "PRODUCT_CONFIG_CONFLICT: use --overwrite-config to change work item provider",
            existing_provider=existing_provider,
            requested_provider=requested_provider,
        )
        return 0

    work_item_provider = requested_provider or existing_provider or "noop"
    existing_pattern = config_pattern or str(registered.get("work_item_id_pattern") or "")
    work_item_id_pattern = args.work_item_id_pattern or existing_pattern or WORK_ITEM_ID_PATTERNS.get(work_item_provider, "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
    product = {
        "id": args.product_id,
        "name": args.product_name,
        "profile": args.profile,
        "root": str(root),
        "workspace": args.workspace,
        "config": f"{args.workspace}/project.yaml",
        "work_item_provider": work_item_provider,
        "work_item_id_pattern": work_item_id_pattern, "assurance": args.assurance,
        "bmad_install_root": ".",
        "bmad_output_root": bmad_output_root({"workspace": args.workspace}),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    assurance = configure_assurance(root, args.assurance)
    if assurance["decision"] != "pass":
        emit("block", assurance["reason"], product=product, created=[])
        return 0
    product = upsert_product(product)
    created = ensure_product_workspace(product, overwrite=args.overwrite_config)
    write_active(product)
    bmad = maybe_install_bmad(product, args.install_bmad)
    if bmad.startswith("BMAD_INSTALL_FAILED"):
        emit("block", "PRODUCT_INITIALIZED_BMAD_INSTALL_FAILED", product=product, created=created, bmad=bmad)
        return 0
    bmad_config = ensure_bmad_output_config(product)
    if "BLOCK" in bmad_config or "CONFLICT" in bmad_config:
        emit("block", "PRODUCT_INITIALIZED_BMAD_CONFIG_BLOCKED", product=product, created=created, bmad=bmad, bmad_config=bmad_config)
        return 0
    emit("pass", "PRODUCT_INITIALIZED", product=product, created=created, bmad=bmad, bmad_config=bmad_config, assurance=assurance)
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    data = load_registry()
    products = data.get("products") or []
    selected = None
    if args.product_id:
        selected = next((p for p in products if p.get("id") == args.product_id), None)
    elif args.product_root:
        root = str(Path(args.product_root).resolve())
        selected = next((p for p in products if str(Path(p.get("root", "")).resolve()) == root), None)
    if not selected:
        emit("block", "PRODUCT_NOT_REGISTERED")
        return 0
    write_active(selected)
    emit("pass", "PRODUCT_ACTIVE", product=selected)
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    active = {}
    if active_path().is_file():
        try:
            active = json.loads(active_path().read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            active = {}
    emit("pass", "PRODUCT_REGISTRY", products=load_registry().get("products") or [], active=active)
    return 0


def product_matches(product: dict[str, Any], product_id: str = "", product_root: str = "") -> bool:
    if product_id and product.get("id") == product_id:
        return True
    if product_root:
        try:
            return Path(str(product.get("root", ""))).resolve() == Path(product_root).resolve()
        except (OSError, RuntimeError):
            return False
    return False


def generated_paths_for_product(product: dict[str, Any]) -> list[Path]:
    root = Path(product["root"]).resolve()
    workspace = str(product.get("workspace") or "ael-workspace").strip("/")
    return [
        root / workspace,
        root / "_bmad",
        root / "_bmad-output",
    ]


def clear_active_if_product(product: dict[str, Any]) -> bool:
    path = active_path()
    if not path.is_file():
        return False
    try:
        active = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        active = {}
    active_root = active.get("root")
    matches = active.get("product_id") == product.get("id")
    if not matches and active_root:
        try:
            matches = Path(active_root).resolve() == Path(str(product.get("root", ""))).resolve()
        except (OSError, RuntimeError):
            matches = False
    if matches:
        path.unlink()
        return True
    return False


def remove_product(args: argparse.Namespace) -> dict[str, Any]:
    if not args.product_id and not args.product_root:
        return {"decision": "block", "reason": "PRODUCT_SELECTOR_REQUIRED"}
    if args.delete_generated and not args.force and not args.dry_run:
        return {
            "decision": "block",
            "reason": "DELETE_GENERATED_REQUIRES_FORCE",
            "hint": "rerun with --delete-generated --force, or use --dry-run to preview",
        }

    data = load_registry()
    products = data.get("products") or []
    selected = next((p for p in products if product_matches(p, args.product_id, args.product_root)), None)
    if not selected:
        return {"decision": "block", "reason": "PRODUCT_NOT_REGISTERED"}

    deleted_paths: list[str] = []
    planned_delete_paths = [str(path) for path in generated_paths_for_product(selected) if path.exists()]
    active_cleared = False
    registry_removed = False

    if not args.dry_run:
        data["products"] = [p for p in products if not product_matches(p, args.product_id, args.product_root)]
        write_registry(data)
        registry_removed = True
        active_cleared = clear_active_if_product(selected)

        if args.delete_generated:
            for path in generated_paths_for_product(selected):
                if path.exists():
                    shutil.rmtree(path)
                    deleted_paths.append(str(path))

    return {
        "decision": "pass",
        "reason": "PRODUCT_REMOVED" if not args.dry_run else "PRODUCT_REMOVE_DRY_RUN",
        "product": selected,
        "registry_removed": registry_removed,
        "active_cleared": active_cleared,
        "delete_generated": bool(args.delete_generated),
        "planned_delete_paths": planned_delete_paths,
        "deleted_paths": deleted_paths,
    }


def cmd_remove(args: argparse.Namespace) -> int:
    result = remove_product(args)
    emit(result.pop("decision"), result.pop("reason"), **result)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--product-root", required=True)
    p_init.add_argument("--product-id", required=True)
    p_init.add_argument("--product-name", required=True)
    p_init.add_argument("--profile", default="generic")
    p_init.add_argument("--workspace", default="ael-workspace")
    p_init.add_argument("--work-item-provider", default="")
    p_init.add_argument("--work-item-id-pattern", default="")
    p_init.add_argument("--overwrite-config", action="store_true")
    p_init.add_argument("--install-bmad", action="store_true")
    p_init.add_argument("--assurance", choices=("local", "guarded"), default="local")
    p_init.set_defaults(func=cmd_init)

    p_use = sub.add_parser("use")
    p_use.add_argument("--product-id", default="")
    p_use.add_argument("--product-root", default="")
    p_use.set_defaults(func=cmd_use)

    p_list = sub.add_parser("list")
    p_list.set_defaults(func=cmd_list)

    p_remove = sub.add_parser("remove")
    p_remove.add_argument("--product-id", default="")
    p_remove.add_argument("--product-root", default="")
    p_remove.add_argument("--delete-generated", action="store_true", help="Delete product-generated AEL directories such as ael-workspace and _bmad.")
    p_remove.add_argument("--force", action="store_true", help="Required with --delete-generated unless --dry-run is set.")
    p_remove.add_argument("--dry-run", action="store_true", help="Preview registry and generated-directory removal without changing files.")
    p_remove.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
