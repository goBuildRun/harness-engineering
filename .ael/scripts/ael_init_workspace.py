#!/usr/bin/env python3
"""Create the canonical AEL workspace from product templates."""
from __future__ import annotations

from pathlib import Path
from typing import Any


WORK_ITEM_ID_PATTERNS = {
    "noop": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$",
    "teambition": "^[0-9a-f]{24}$",
    "feishu": "^[A-Za-z0-9_-]{8,128}$",
    "jira": "^[A-Z][A-Z0-9]+-[0-9]+$",
}


def ael_root() -> Path:
    return Path(__file__).resolve().parents[2]


def workspace_templates_dir() -> Path:
    return ael_root() / ".ael" / "templates" / "product-workspace"


def safe_profile_name(profile: Any) -> str:
    raw = str(profile or "generic").strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_")).lower()
    return safe or "generic"


def template_path(name: str, product: dict[str, Any]) -> Path:
    profile = safe_profile_name(product.get("profile"))
    profile_path = workspace_templates_dir() / "profiles" / profile / name
    if profile_path.is_file():
        return profile_path
    return workspace_templates_dir() / name


def render_template(name: str, product: dict[str, Any]) -> str:
    workspace = product.get("workspace") or "ael-workspace"
    profile = product.get("profile") or "generic"
    text = template_path(name, product).read_text(encoding="utf-8")
    replacements = {
        "{{product_id}}": str(product["id"]),
        "{{product_name}}": str(product["name"]),
        "{{profile}}": str(profile),
        "{{workspace}}": str(workspace),
        "{{work_item_provider}}": str(product.get("work_item_provider") or "noop"),
        "{{work_item_id_pattern}}": str(product.get("work_item_id_pattern") or WORK_ITEM_ID_PATTERNS["noop"]),
        "{{assurance_level}}": str(product.get("assurance") or "local"),
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def write_template_if_missing(
    root: Path,
    rel: str,
    template_name: str,
    product: dict[str, Any],
    created: list[str],
) -> None:
    path = root / rel
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_template(template_name, product), encoding="utf-8")
    created.append(str(path.relative_to(root)))


def write_text_if_missing(root: Path, rel: str, text: str, created: list[str]) -> None:
    path = root / rel
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    created.append(str(path.relative_to(root)))


def ensure_product_workspace(product: dict[str, Any], overwrite: bool = False) -> list[str]:
    root = Path(product["root"]).resolve()
    workspace = product.get("workspace") or "ael-workspace"
    workspace_root = root / workspace
    created: list[str] = []
    for rel in (
        "planning/product-specs",
        "planning/exec-plans/active",
        "planning/exec-plans/completed",
        "planning/tasks",
        "runs",
        "knowledge",
        "evidence/summaries",
        "evidence/progress",
        "evidence/test-reports",
        "evidence/review-reports",
        "evidence/growth-reports",
        "evidence/intake-reports",
        "bmad-output/planning-artifacts",
        "bmad-output/design-artifacts",
        "bmad-output/implementation-artifacts",
        "bmad-output/test-artifacts",
    ):
        path = workspace_root / rel
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(path.relative_to(root)))

    config = root / (product.get("config") or f"{workspace}/project.yaml")
    if overwrite or not config.exists():
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(render_template("project.yaml", product), encoding="utf-8")
        created.append(str(config.relative_to(root)))

    write_template_if_missing(root, f"{workspace}/README.md", "README.md", product, created)
    write_template_if_missing(root, f"{workspace}/planning/README.md", "planning-README.md", product, created)
    write_template_if_missing(root, f"{workspace}/planning/product-specs/index.md", "product-specs-index.md", product, created)
    write_template_if_missing(root, f"{workspace}/knowledge/CONTEXT.md", "CONTEXT.md", product, created)
    write_template_if_missing(root, f"{workspace}/knowledge/LESSONS.md", "LESSONS.md", product, created)
    write_text_if_missing(
        root,
        f"{workspace}/runs/.gitignore",
        "# Local AEL execution state. Evidence that must be reviewed belongs in ../evidence/.\n*\n!.gitignore\n",
        created,
    )
    return created
