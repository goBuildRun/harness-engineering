#!/usr/bin/env python3
"""Bounded Python import discovery for gate dependency manifests."""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import re
from pathlib import Path

from harness_gate_manifest import GateInputError


def python_module_executes(
    module: str, target: Path, cwd: Path, pythonpath: str,
    interpreter: Path | None, options: frozenset[str] = frozenset(),
) -> bool:
    relative = Path(*module.split(".")) if module else Path("__missing__")
    isolated = "-I" in options
    roots = [] if isolated or "-P" in options else [cwd]
    if not isolated and "-E" not in options:
        roots.extend(
            Path(item) if Path(item).is_absolute() else cwd / item
            for item in pythonpath.split(os.pathsep) if item
        )
    if interpreter is not None and "-S" not in options:
        prefixes = {
            interpreter.parent.parent,
            interpreter.resolve(strict=False).parent.parent,
        }
        versions = set(re.findall(
            r"(?:python@?|Versions/)(\d+\.\d+)",
            "\n".join(str(item) for item in (interpreter, interpreter.resolve(strict=False))),
        ))
        for prefix in prefixes:
            try:
                config = (prefix / "pyvenv.cfg").read_text(encoding="utf-8")
            except OSError:
                continue
            versions.update(re.findall(r"(?m)^version(?:_info)?\s*=\s*(\d+\.\d+)", config))
        for prefix in prefixes:
            for version in versions:
                roots.extend(
                    lib / f"python{version}" / kind
                    for lib in (prefix / "lib", prefix / "lib64")
                    for kind in ("site-packages", "dist-packages")
                )
    for root in dict.fromkeys(roots):
        module_file = root / relative.with_suffix(".py")
        package = root / relative
        if module_file.is_file():
            return module_file.resolve(strict=False) == target
        if package.is_dir():
            return target in {
                candidate.resolve(strict=False)
                for candidate in (package / "__init__.py", package / "__main__.py")
                if candidate.is_file()
            }
    return False


def python_modules(
    tree: ast.AST, *, allow_manifest_bound_dynamic: bool = False,
    check_deadline=None, on_dynamic=None,
) -> set[str]:
    modules: set[str] = set()
    importlib_names = {"importlib"}
    builtins_names = {"builtins"}
    import_module_names = {"__import__", "import_module"}
    for node in ast.walk(tree):
        if check_deadline is not None:
            check_deadline()
        if isinstance(node, ast.Import):
            importlib_names.update(
                alias.asname or alias.name for alias in node.names
                if alias.name == "importlib"
            )
            builtins_names.update(
                alias.asname or alias.name for alias in node.names
                if alias.name == "builtins"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            import_module_names.update(
                alias.asname or alias.name for alias in node.names
                if alias.name == "import_module"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            import_module_names.update(
                alias.asname or alias.name for alias in node.names
                if alias.name == "__import__"
            )
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            loader = (
                isinstance(value, ast.Name) and value.id in import_module_names
                or isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                and (
                    value.value.id in importlib_names and value.attr == "import_module"
                    or value.value.id in builtins_names and value.attr == "__import__"
                )
            )
            if not loader:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in import_module_names:
                    import_module_names.add(target.id)
                    changed = True
    for node in ast.walk(tree):
        if check_deadline is not None:
            check_deadline()
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name) and node.func.id in import_module_names
            or isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and (
                node.func.value.id in importlib_names and node.func.attr == "import_module"
                or node.func.value.id in builtins_names and node.func.attr == "__import__"
            )
        ):
            if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(
                node.args[0].value, str,
            ):
                if allow_manifest_bound_dynamic:
                    if on_dynamic is not None:
                        on_dynamic()
                    continue
                raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_IMPORT")
            module = node.args[0].value
            if module.startswith("."):
                package = node.args[1] if len(node.args) > 1 else next((
                    keyword.value for keyword in node.keywords if keyword.arg == "package"
                ), None)
                if not isinstance(package, ast.Constant) or not isinstance(package.value, str):
                    if allow_manifest_bound_dynamic:
                        if on_dynamic is not None:
                            on_dynamic()
                        continue
                    raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_IMPORT")
                try:
                    module = importlib.util.resolve_name(module, package.value)
                except (ImportError, ValueError) as exc:
                    raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_IMPORT") from exc
            modules.add(module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (
            node.func.id in {"eval", "exec"}
        ):
            argument = node.args[0] if node.args else None
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                try:
                    nested = ast.parse(argument.value)
                except (MemoryError, RecursionError, SyntaxError, ValueError):
                    nested = None
                if nested is not None:
                    modules.update(python_modules(
                        nested, allow_manifest_bound_dynamic=allow_manifest_bound_dynamic,
                        check_deadline=check_deadline, on_dynamic=on_dynamic,
                    ))
                    continue
            static_path_read = (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Attribute)
                and argument.func.attr in {"read_bytes", "read_text"}
                and isinstance(argument.func.value, ast.Call)
                and isinstance(argument.func.value.func, ast.Name)
                and argument.func.value.func.id == "Path"
                and argument.func.value.args
                and isinstance(argument.func.value.args[0], ast.Constant)
                and isinstance(argument.func.value.args[0].value, str)
            )
            if static_path_read:
                continue
            if allow_manifest_bound_dynamic:
                if on_dynamic is not None:
                    on_dynamic()
                continue
            raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_IMPORT")
    return modules


def add_import_targets(
    dependencies: set[Path], module: str, roots: list[Path], manifest=None,
) -> None:
    parts = module.split(".")
    for root in roots:
        if manifest is not None:
            manifest.check_deadline()
        base = root.joinpath(*parts)
        dependencies.update(
            root.joinpath(*parts[:depth], "__init__.py")
            for depth in range(1, len(parts))
        )
        dependencies.update((base, base / "__init__.py", base / "__main__.py"))
        dependencies.update(
            base.parent / f"{base.name}{suffix}"
            for suffix in importlib.machinery.all_suffixes()
        )
        if manifest is not None:
            children = manifest.dependency_children(base.parent)
        elif base.parent.is_dir():
            children = base.parent.iterdir()
        else:
            children = ()
        for child in children:
            if manifest is not None:
                manifest.check_deadline()
            if child.name.startswith(f"{base.name}.") and child.suffix in {".so", ".pyd"}:
                dependencies.add(child)
