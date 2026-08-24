#!/usr/bin/env python3
"""BMAD installation and managed output configuration for product init."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

BMAD_CONFIG_BEGIN = "# BEGIN buildrun-agent-engineering-lifecycle managed BMAD output"
BMAD_CONFIG_END = "# END buildrun-agent-engineering-lifecycle managed BMAD output"
LEGACY_BMAD_CONFIG_MARKERS = (
    (
        "# BEGIN harness-engineering managed BMAD output",
        "# END harness-engineering managed BMAD output",
    ),
    (
        "# BEGIN agent-engineering-lifecycle managed BMAD output",
        "# END agent-engineering-lifecycle managed BMAD output",
    ),
)


def bmad_output_root(product: dict[str, Any]) -> str:
    workspace = str(product.get("workspace") or "ael-workspace").strip("/")
    return f"{workspace}/bmad-output"


def maybe_install_bmad(product: dict[str, Any], install: bool) -> str:
    root = Path(product["root"]).resolve()
    if (root / "_bmad").exists():
        return "BMAD_PRESENT"
    if not install:
        return "BMAD_NOT_INSTALLED: init skipped BMAD install because --install-bmad was not set; rerun init with --install-bmad or run `npx bmad-method install` in product root"
    modules = os.environ.get("AEL_BMAD_MODULES", "bmm,tea")
    tools = os.environ.get("AEL_BMAD_TOOLS", "codex,cursor")
    communication_language = os.environ.get("AEL_BMAD_COMMUNICATION_LANGUAGE", "Chinese")
    document_language = os.environ.get("AEL_BMAD_DOCUMENT_LANGUAGE", "Chinese")
    user_name = os.environ.get("AEL_BMAD_USER_NAME", str(product.get("name") or product.get("id") or "BMad"))
    output_folder = bmad_output_root(product)
    cmd = [
        "npx",
        "bmad-method",
        "install",
        "--directory",
        str(root),
        "--modules",
        modules,
        "--tools",
        tools,
        "--output-folder",
        output_folder,
        "--communication-language",
        communication_language,
        "--document-output-language",
        document_language,
        "--user-name",
        user_name,
        "--yes",
    ]
    try:
        proc = subprocess.run(cmd, cwd=root, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        return "BMAD_INSTALL_FAILED: command_not_found=npx"
    log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()[-12:]
    tail = " ".join(line.strip() for line in log if line.strip())[:1200]
    if proc.returncode != 0:
        return f"BMAD_INSTALL_FAILED: exit={proc.returncode} log={tail}"
    if not (root / "_bmad").is_dir():
        return f"BMAD_INSTALL_FAILED: _bmad not created; installer_exit=0 log={tail}"
    return f"BMAD_INSTALLED: modules={modules} tools={tools}"


def render_bmad_config_block(product: dict[str, Any]) -> str:
    root = bmad_output_root(product)
    q = json.dumps
    return f"""{BMAD_CONFIG_BEGIN}
# BMAD installer only accepts the project root. buildrun-agent-engineering-lifecycle pins
# all BMAD generated artifacts back into the product-owned workspace here.

[core]
output_folder = {q(f"{{project-root}}/{root}")}

[modules.bmm]
planning_artifacts = {q(f"{{project-root}}/{root}/planning-artifacts")}
design_artifacts = {q(f"{{project-root}}/{root}/design-artifacts")}
implementation_artifacts = {q(f"{{project-root}}/{root}/implementation-artifacts")}

[modules.cis]
design_artifacts = {q(f"{{project-root}}/{root}/design-artifacts")}

[modules.tea]
test_artifacts = {q(f"{{project-root}}/{root}/test-artifacts")}
test_design_output = {q(f"{root}/test-artifacts/test-design")}
test_review_output = {q(f"{root}/test-artifacts/test-reviews")}
trace_output = {q(f"{root}/test-artifacts/traceability")}
{BMAD_CONFIG_END}
"""


def upsert_managed_block(text: str, block: str) -> str:
    marker_pairs = ((BMAD_CONFIG_BEGIN, BMAD_CONFIG_END), *LEGACY_BMAD_CONFIG_MARKERS)
    for begin, end in marker_pairs:
        if begin in text and end in text:
            before, rest = text.split(begin, 1)
            _old, after = rest.split(end, 1)
            return before.rstrip() + "\n\n" + block.rstrip() + "\n" + after.lstrip("\n")
    prefix = text.rstrip()
    return (prefix + "\n\n" if prefix else "") + block


def merge_legacy_bmad_output(src: Path, dst: Path) -> list[str]:
    conflicts: list[str] = []
    if not src.exists():
        return conflicts
    dst.mkdir(parents=True, exist_ok=True)
    for child in list(src.iterdir()):
        target = dst / child.name
        if child.is_dir() and target.is_dir():
            conflicts.extend(merge_legacy_bmad_output(child, target))
            try:
                child.rmdir()
            except OSError:
                pass
        elif target.exists():
            conflicts.append(str(child))
        else:
            shutil.move(str(child), str(target))
    try:
        src.rmdir()
    except OSError:
        pass
    return conflicts


def ensure_bmad_output_config(product: dict[str, Any]) -> str:
    root = Path(product["root"]).resolve()
    workspace = str(product.get("workspace") or "ael-workspace").strip("/")
    bmad_dir = root / "_bmad"
    output_dir = root / bmad_output_root(product)
    for rel in ("planning-artifacts", "design-artifacts", "implementation-artifacts", "test-artifacts"):
        (output_dir / rel).mkdir(parents=True, exist_ok=True)

    legacy = root / "_bmad-output"
    conflicts = merge_legacy_bmad_output(legacy, output_dir)
    if conflicts:
        return "BMAD_OUTPUT_MIGRATION_CONFLICT: " + ",".join(conflicts)

    if not bmad_dir.is_dir():
        return "BMAD_CONFIG_SKIPPED: _bmad not installed"

    custom = bmad_dir / "custom" / "config.toml"
    custom.parent.mkdir(parents=True, exist_ok=True)
    text = custom.read_text(encoding="utf-8") if custom.exists() else ""
    custom.write_text(upsert_managed_block(text, render_bmad_config_block(product)), encoding="utf-8")

    resolver = bmad_dir / "scripts" / "resolve_config.py"
    if not resolver.is_file():
        return "BMAD_CONFIG_BLOCK: missing _bmad/scripts/resolve_config.py"
    proc = subprocess.run(
        [
            "python3",
            str(resolver),
            "--project-root",
            str(root),
            "--key",
            "core.output_folder",
            "--key",
            "modules.bmm.planning_artifacts",
            "--key",
            "modules.bmm.design_artifacts",
            "--key",
            "modules.tea.test_artifacts",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return f"BMAD_CONFIG_BLOCK: resolve_config failed exit={proc.returncode}"
    try:
        resolved = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return "BMAD_CONFIG_BLOCK: resolve_config returned non-json"
    expected = {
        "core.output_folder": f"{{project-root}}/{workspace}/bmad-output",
        "modules.bmm.planning_artifacts": f"{{project-root}}/{workspace}/bmad-output/planning-artifacts",
        "modules.bmm.design_artifacts": f"{{project-root}}/{workspace}/bmad-output/design-artifacts",
        "modules.tea.test_artifacts": f"{{project-root}}/{workspace}/bmad-output/test-artifacts",
    }
    mismatches = [f"{key}!={value}" for key, value in expected.items() if resolved.get(key) != value]
    if mismatches:
        return "BMAD_CONFIG_BLOCK: " + ";".join(mismatches)
    return f"BMAD_CONFIGURED: {workspace}/bmad-output"
