#!/usr/bin/env python3
"""Run product-configured lint/test commands and emit Harness JSON."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from harness_output import dump_json
from workspace_paths import load_layout
from sandbox_exec import reject as sandbox_reject

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


SHELL_CONTROL = re.compile(r"[;&|><`\n\r]")
PY_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "_bmad", "harness-workspace"}
DEFAULT_ALLOWED_EXECUTABLES = {
    "python",
    "python3",
    "pytest",
    "ruff",
    "mypy",
    "pyright",
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "node",
    "go",
    "cargo",
    "uv",
}
DENIED_ENV_KEYS = {
    "PATH",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
    "BASH_ENV",
    "ENV",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "NODE_OPTIONS",
    "RUBYOPT",
}
PYTHON_ALLOWED_MODULES = {"compileall", "mypy", "pytest", "ruff", "unittest"}
NODE_INLINE_OPTIONS = {"-e", "--eval", "-p", "--print"}
PACKAGE_SCRIPT_ALLOWLIST = {
    "build",
    "check",
    "format:check",
    "lint",
    "test",
    "test:ci",
    "typecheck",
}
def emit(decision: str, reason: str, **extra: Any) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None or not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def product_project_config(product_root: Path) -> dict[str, Any]:
    for rel in (
        "harness-workspace/project.yaml",
        "harness-workspace/config.yaml",
        ".harness-engineering.yaml",
    ):
        data = load_yaml(product_root / rel)
        if data:
            return data
    return {}


def normalize_command(item: Any) -> tuple[str, list[str], dict[str, str]]:
    env: dict[str, str] = {}
    name = ""
    raw = item
    if isinstance(item, dict):
        name = str(item.get("name") or "")
        env = {str(k): str(v) for k, v in (item.get("env") or {}).items()}
        raw = item.get("cmd") or item.get("command") or []
    if isinstance(raw, str):
        if SHELL_CONTROL.search(raw):
            raise ValueError("QUALITY_COMMAND_UNSAFE: 禁止 shell 控制符；请使用 argv 列表或单一命令")
        argv = shlex.split(raw)
    elif isinstance(raw, list) and all(isinstance(part, (str, int, float)) for part in raw):
        argv = [str(part) for part in raw]
    else:
        raise ValueError(f"QUALITY_COMMAND_INVALID: {item!r}")
    if not argv:
        raise ValueError("QUALITY_COMMAND_EMPTY")
    if not name:
        name = shlex.join(argv)
    return name, argv, env


def allowed_executables() -> set[str]:
    extra = {
        item.strip()
        for item in os.environ.get("HARNESS_QUALITY_ALLOWED_EXECUTABLES", "").split(",")
        if item.strip()
    }
    return DEFAULT_ALLOWED_EXECUTABLES | extra


def validate_env(env: dict[str, str]) -> str | None:
    denied = sorted(k for k in env if k in DENIED_ENV_KEYS)
    if denied:
        return "QUALITY_COMMAND_UNSAFE_ENV: 禁止在产品配置中覆盖 " + ",".join(denied)
    return None


def validate_package_script(argv: list[str]) -> str | None:
    exe = os.path.basename(argv[0])
    if exe == "npm":
        if len(argv) >= 2 and argv[1] == "test":
            return None
        if len(argv) >= 3 and argv[1] == "run" and argv[2] in PACKAGE_SCRIPT_ALLOWLIST:
            return None
        return "QUALITY_COMMAND_UNSAFE: npm 仅允许 test 或 run lint/test/typecheck/build/check/format:check"
    if exe in {"pnpm", "yarn", "bun"}:
        if len(argv) >= 2 and argv[1] in PACKAGE_SCRIPT_ALLOWLIST:
            return None
        if len(argv) >= 3 and argv[1] == "run" and argv[2] in PACKAGE_SCRIPT_ALLOWLIST:
            return None
        return f"QUALITY_COMMAND_UNSAFE: {exe} 仅允许 lint/test/typecheck/build/check/format:check"
    return None


def validate_command_argv(argv: list[str], allowed: set[str]) -> str | None:
    sandbox_violation = sandbox_reject(argv)
    if sandbox_violation:
        return sandbox_violation.replace("VIOLATION_SANDBOX", "QUALITY_COMMAND_UNSAFE")

    exe = os.path.basename(argv[0])
    if Path(argv[0]).name != argv[0]:
        return "QUALITY_COMMAND_UNSAFE: 禁止使用带路径的可执行文件；请使用 allowlist 中的命令名"
    if exe not in allowed:
        return f"QUALITY_COMMAND_UNSAFE_EXECUTABLE: {exe} 不在 HARNESS quality allowlist"

    if exe in {"python", "python3"}:
        if any(part in {"-c", "--command"} for part in argv[1:]):
            return "QUALITY_COMMAND_UNSAFE: 禁止 python -c"
        if len(argv) >= 3 and argv[1] == "-m" and argv[2] not in PYTHON_ALLOWED_MODULES:
            return f"QUALITY_COMMAND_UNSAFE: python -m {argv[2]} 未在模块 allowlist"

    if exe == "node" and any(part in NODE_INLINE_OPTIONS for part in argv[1:]):
        return "QUALITY_COMMAND_UNSAFE: 禁止 node inline eval/print"

    if exe in {"npm", "pnpm", "yarn", "bun"}:
        return validate_package_script(argv)

    if exe == "uv":
        if len(argv) >= 3 and argv[1] == "run":
            return validate_command_argv(argv[2:], allowed)
        return "QUALITY_COMMAND_UNSAFE: uv 仅允许 uv run <allowed-command>"

    return None


def commands_for(config: dict[str, Any], kind: str) -> tuple[list[Any], dict[str, str], bool]:
    quality = config.get("quality") or {}
    commands = quality.get("commands") or {}
    raw = commands.get(kind) or quality.get(kind) or []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    env = {str(k): str(v) for k, v in (quality.get("env") or {}).items()}
    required_cfg = quality.get("required") or {}
    required = bool(required_cfg.get(kind, False))
    return list(raw), env, required


def run_command(product_root: Path, name: str, argv: list[str], extra_env: dict[str, str], timeout: int) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(extra_env)
    try:
        proc = subprocess.run(
            argv,
            cwd=product_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"name": name, "argv": argv, "ok": False, "reason": f"command_not_found={argv[0]}"}
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "").splitlines()[-25:]
        return {"name": name, "argv": argv, "ok": False, "reason": f"timeout={timeout}s", "log": " ".join(output)[:2000]}

    output = (proc.stdout or "").splitlines()[-25:]
    return {
        "name": name,
        "argv": argv,
        "ok": proc.returncode == 0,
        "exit": proc.returncode,
        "log": " ".join(output)[:2000],
    }


def iter_python_files(product_root: Path, roots: list[str]) -> list[Path]:
    product_root = product_root.resolve()
    files: list[Path] = []
    for raw in roots:
        root = (product_root / raw).resolve()
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*.py")
        for path in candidates:
            if not path.is_file() or path.suffix != ".py":
                continue
            rel_parts = path.relative_to(product_root).parts
            if any(part in PY_SKIP_DIRS for part in rel_parts):
                continue
            files.append(path)
    return sorted(files)


def run_builtin(product_root: Path, item: dict[str, Any]) -> dict[str, Any]:
    product_root = product_root.resolve()
    name = str(item.get("name") or item.get("builtin") or "builtin")
    builtin = str(item.get("builtin") or "")
    if builtin not in {"python-syntax", "python-import-boundaries"}:
        return {"name": name, "ok": False, "reason": f"unsupported_builtin={builtin}"}
    roots = [str(p) for p in (item.get("paths") or ["services", "tests"])]
    files = iter_python_files(product_root, roots)
    if builtin == "python-import-boundaries":
        return run_import_boundaries(product_root, files, item, name)
    issues: list[str] = []
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            rel = path.relative_to(product_root)
            issues.append(f"{rel}:{exc.lineno}:{exc.msg}")
        except OSError as exc:
            rel = path.relative_to(product_root)
            issues.append(f"{rel}:{exc}")
    return {"name": name, "builtin": builtin, "ok": not issues, "files": len(files), "issues": issues[:20]}


def imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
    return imports


def run_import_boundaries(product_root: Path, files: list[Path], item: dict[str, Any],
                          name: str) -> dict[str, Any]:
    rules = item.get("boundaries") or []
    if not isinstance(rules, list) or not rules:
        return {"name": name, "builtin": "python-import-boundaries", "ok": False,
                "reason": "boundaries_required"}
    issues: list[str] = []
    for path in files:
        rel = path.relative_to(product_root).as_posix()
        applicable = [rule for rule in rules if isinstance(rule, dict)
                      and rel.startswith(str(rule.get("from") or "").rstrip("/") + "/")]
        if not applicable:
            continue
        try:
            imports = imported_modules(ast.parse(path.read_text(encoding="utf-8"), filename=rel))
        except (OSError, SyntaxError) as exc:
            issues.append(f"{rel}:{exc}")
            continue
        for rule in applicable:
            forbidden = [str(value).strip(".") for value in (rule.get("forbid") or [])]
            for line, module in imports:
                if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                    issues.append(f"{rel}:{line}: forbidden import {module}")
    return {
        "name": name, "builtin": "python-import-boundaries", "ok": not issues,
        "files": len(files), "issues": issues[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("lint", "test"))
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--product-id", default="")
    parser.add_argument("--strict", action="store_true", help="未配置命令时 block")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("HARNESS_QUALITY_TIMEOUT_SECONDS") or "600"))
    args = parser.parse_args()

    product_root_arg = args.product_root
    layout = load_layout(
        Path(args.harness_root).resolve(),
        Path(product_root_arg).resolve() if product_root_arg else None,
        args.product_id,
    )
    config = product_project_config(layout.product_root)
    raw_commands, shared_env, required = commands_for(config, args.kind)
    strict = args.strict or required or os.environ.get("HARNESS_QUALITY_STRICT", "") in {"1", "true", "yes"}

    if not raw_commands:
        decision = "block" if strict else "pass"
        reason = f"QUALITY_{args.kind.upper()}_UNCONFIGURED: 产品侧 project.yaml 未配置 quality.commands.{args.kind}"
        emit(decision, reason, product=layout.product_name, product_root=str(layout.product_root))
        return 0

    results: list[dict[str, Any]] = []
    for item in raw_commands:
        if isinstance(item, dict) and item.get("builtin"):
            result = run_builtin(layout.product_root, item)
            results.append(result)
            if not result["ok"]:
                emit("block", f"QUALITY_{args.kind.upper()}_FAILED: {result['name']}", results=results)
                return 0
            continue
        try:
            name, argv, item_env = normalize_command(item)
        except ValueError as exc:
            emit("block", str(exc), command=item)
            return 0
        merged_env = {**shared_env, **item_env}
        env_violation = validate_env(merged_env)
        if env_violation:
            emit("block", env_violation, command=name)
            return 0
        argv_violation = validate_command_argv(argv, allowed_executables())
        if argv_violation:
            emit("block", argv_violation, command=name, argv=argv)
            return 0
        result = run_command(layout.product_root, name, argv, merged_env, args.timeout)
        results.append(result)
        if not result["ok"]:
            emit("block", f"QUALITY_{args.kind.upper()}_FAILED: {name}", results=results)
            return 0

    emit("pass", f"QUALITY_{args.kind.upper()}_OK: {len(results)} command(s) passed", results=results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
