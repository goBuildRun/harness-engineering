#!/usr/bin/env python3
"""Interpreter startup roots and .pth bindings for gate fingerprints."""
from __future__ import annotations

import ast
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Callable

from harness_gate_dependency_io import dependency_text
from harness_gate_imports import add_import_targets, python_modules
from harness_gate_language import absolute_search_path, check_language_source
from harness_gate_manifest import CandidateManifest, GateInputError


def interpreter_python_roots(interpreter: Path, manifest=None) -> list[Path]:
    lexical = interpreter.resolve(strict=False) if not interpreter.is_absolute() else interpreter
    resolved = lexical.resolve(strict=False)
    prefixes = [lexical.parent.parent, resolved.parent.parent]
    prefixes.extend(
        Path(value).resolve(strict=False)
        for name in ("VIRTUAL_ENV", "CONDA_PREFIX")
        if (value := os.environ.get(name))
    )
    versions = set(re.findall(r"(?:python@?|Versions/)(\d+\.\d+)", str(resolved)))
    discovered: list[Path] = []
    for prefix in dict.fromkeys(prefixes):
        manifest.check_deadline()
        for lib in (prefix / "lib", prefix / "lib64"):
            manifest.check_deadline()
            for python_root in manifest.external_children(lib):
                manifest.check_deadline()
                if not python_root.name.startswith("python"):
                    continue
                if versions and not any(version in python_root.name for version in versions):
                    continue
                for child in (
                    python_root / "site-packages", python_root / "dist-packages",
                ):
                    manifest.check_deadline()
                    manifest.external_children(child)
                    if child.is_dir():
                        discovered.append(child)
    return list(dict.fromkeys(discovered))


class PythonEnvironment:
    def __init__(
        self, harness: Path, product: Path, execution_cwd: Path,
        manifest: CandidateManifest, record: Callable[..., None], scripts: Path,
    ) -> None:
        self.harness = harness
        self.product = product
        self.manifest = manifest
        self.record = record
        self.roots = [execution_cwd, scripts, product]
        self.roots.extend(
            (Path(item) if Path(item).is_absolute() else execution_cwd / item).resolve(
                strict=False,
            )
            for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item
        )
        self.roots.extend(
            Path(item).resolve(strict=False) for item in sys.path
            if item and Path(item).name in {"site-packages", "dist-packages"}
        )
        self.roots = list(dict.fromkeys(self.roots))
        self.import_modules: set[str] = set()
        self._pth_roots_seen: set[Path] = set()

    def add_root(self, root: Path) -> None:
        root = root.resolve(strict=False)
        if root not in self.roots:
            self.roots.append(root)
        if root in self._pth_roots_seen or root.name not in {"site-packages", "dist-packages"}:
            return
        self._pth_roots_seen.add(root)
        if not root.is_dir():
            return
        def discover_pth() -> tuple[Path, ...]:
            selected: list[Path] = []
            for child in self.manifest.external_children(root):
                self.manifest.check_deadline()
                if child.suffix == ".pth" and child.is_file():
                    selected.append(child)
            return tuple(selected)
        pth_files = self.manifest.memoized(
            ("python-pth", str(root)), discover_pth,
        )
        for pth in pth_files:
            self.manifest.check_deadline()
            self.record(pth, label=f"python-path:{pth}")
            for raw_line in dependency_text(
                pth, self.harness, self.product, self.manifest,
            ).splitlines():
                self.manifest.check_deadline()
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(("import ", "import\t")):
                    self._add_pth_import(line)
                    continue
                candidate = Path(line)
                self.add_root(candidate if candidate.is_absolute() else root / candidate)

    def _add_pth_import(self, line: str) -> None:
        check_language_source(line)
        self.manifest.check_deadline()
        try:
            tree = ast.parse(line)
        except (MemoryError, RecursionError, SyntaxError, ValueError) as exc:
            raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_PATH") from exc
        self.manifest.check_deadline()
        if any(not isinstance(statement, (ast.Import, ast.ImportFrom)) for statement in tree.body):
            raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_PATH")
        modules = python_modules(tree, check_deadline=self.manifest.check_deadline)
        if not modules:
            raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_PATH")
        self.import_modules.update(modules)

    def add_interpreter(self, interpreter: Path) -> None:
        prefix = interpreter.resolve(strict=False).parent.parent
        lexical_prefix = interpreter.parent.resolve(strict=False).parent
        for config in dict.fromkeys((prefix / "pyvenv.cfg", lexical_prefix / "pyvenv.cfg")):
            self.record(config, label=f"python-config:{config}")
        roots = self.manifest.memoized(
            ("python-interpreter-roots", str(interpreter.resolve(strict=False))),
            lambda: tuple(interpreter_python_roots(interpreter, self.manifest)),
        )
        for root in roots:
            self.add_root(root)

    def bind_import(
        self, dependencies: set[Path], module: str, roots: list[Path],
    ) -> None:
        key = ("python-import-target", "\0".join([module, *(str(root) for root in roots)]))

        def discover() -> frozenset[Path]:
            targets: set[Path] = set()
            add_import_targets(targets, module, roots, self.manifest)
            return frozenset(targets)

        dependencies.update(self.manifest.memoized(key, discover))

    def bind_context_imports(
        self, dependencies: set[Path],
        bindings: tuple[
            tuple[str, tuple[Path, str, str], bool, Path | None, frozenset[str]], ...
        ],
    ) -> None:
        for module, context, _guaranteed, _interpreter, _options in bindings:
            cwd, _path_value, pythonpath_value = context
            roots = [
                cwd,
                *(
                    Path(item) if Path(item).is_absolute() else cwd / item
                    for item in pythonpath_value.split(os.pathsep) if item
                ),
                *self.roots,
            ]
            self.bind_import(dependencies, module, list(dict.fromkeys(roots)))


def bind_path_executable(
    dependencies: set[Path], token: str, *, cwd: Path, path_value: str,
    context: tuple[Path, str, str],
    dependency_contexts: dict[Path, set[tuple[Path, str, str]]], strict: bool,
) -> None:
    raw = Path(token).expanduser()
    if raw.is_absolute() or "/" in token:
        selected = raw if raw.is_absolute() else cwd / raw
        if not selected.is_file() and strict:
            raise GateInputError("GATE_INPUT_EXECUTABLE_NOT_FOUND")
    else:
        found = shutil.which(token, path=absolute_search_path(path_value, cwd))
        if not found:
            if strict:
                raise GateInputError("GATE_INPUT_EXECUTABLE_NOT_FOUND")
            return
        selected = Path(found)
    resolved = selected.resolve(strict=False)
    dependencies.add(resolved)
    dependency_contexts.setdefault(resolved, set()).add(context)


def source_dependencies(
    text: str, path: Path, roots: list[Path], bind_import: Callable[..., None], *,
    allow_dynamic: bool, fail_on_parse: bool,
    bind_executable: Callable[[set[Path], str], None] | None = None,
    allow_dynamic_commands: bool | None = None,
    check_deadline: Callable[[], None] | None = None,
    on_dynamic_import: Callable[[], None] | None = None,
) -> frozenset[Path]:
    check_language_source(text)
    if check_deadline is not None:
        check_deadline()
    try:
        tree = ast.parse(text)
    except (MemoryError, RecursionError, SyntaxError, ValueError) as exc:
        if fail_on_parse:
            raise GateInputError("GATE_INPUT_PYTHON_PARSE_FAILED") from exc
        if on_dynamic_import is not None:
            on_dynamic_import()
        return frozenset()
    if check_deadline is not None:
        check_deadline()
    dependencies: set[Path] = set()
    dynamic_commands_allowed = (
        allow_dynamic if allow_dynamic_commands is None else allow_dynamic_commands
    )
    subprocess_modules = {"subprocess"}
    subprocess_functions: set[str] = set()
    subprocess_names = {"call", "check_call", "check_output", "Popen", "run"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            subprocess_modules.update(
                alias.asname or alias.name for alias in node.names
                if alias.name == "subprocess"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            subprocess_functions.update(
                alias.asname or alias.name for alias in node.names
                if alias.name in subprocess_names
            )
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            subprocess_loader = (
                isinstance(value, ast.Name) and value.id in subprocess_functions
                or isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                and value.value.id in subprocess_modules and value.attr in subprocess_names
            )
            if not subprocess_loader:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in subprocess_functions:
                    subprocess_functions.add(target.id)
                    changed = True
    for module in python_modules(
        tree, allow_manifest_bound_dynamic=allow_dynamic,
        check_deadline=check_deadline, on_dynamic=on_dynamic_import,
    ):
        if check_deadline is not None:
            check_deadline()
        bind_import(dependencies, module, roots)
    for node in ast.walk(tree):
        if check_deadline is not None:
            check_deadline()
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name != "*":
                    bind_import(dependencies, f"{node.module}.{alias.name}", roots)
        if isinstance(node, ast.ImportFrom) and node.level:
            base = path.parent
            for _level in range(node.level - 1):
                base = base.parent
            if node.module:
                base /= node.module.replace(".", "/")
            dependencies.add(base)
            dependencies.update(base / alias.name for alias in node.names if alias.name != "*")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            token = node.value.strip()
            if token and not any(char in token for char in "\n\r\0$`{}"):
                raw = Path(token)
                for root in (path.parent, *roots):
                    if check_deadline is not None:
                        check_deadline()
                    candidate = raw if raw.is_absolute() else root / raw
                    if candidate.is_file() and (
                        candidate.suffix in {".py", ".sh"} or os.access(candidate, os.X_OK)
                    ):
                        dependencies.add(candidate)
        if not isinstance(node, ast.Call) or bind_executable is None:
            continue
        subprocess_call = (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in subprocess_modules
            and node.func.attr in subprocess_names
            or isinstance(node.func, ast.Name)
            and node.func.id in subprocess_functions
        )
        if not subprocess_call:
            continue
        shell_keyword = next((
            keyword.value for keyword in node.keywords if keyword.arg == "shell"
        ), None)
        if isinstance(shell_keyword, ast.Constant) and shell_keyword.value is True:
            if not dynamic_commands_allowed:
                raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_COMMAND")
            continue
        override = next((
            keyword.value for keyword in node.keywords if keyword.arg == "executable"
        ), None)
        command = node.args[0] if node.args else None
        if override is not None and not (
            isinstance(override, ast.Constant) and override.value is None
        ):
            executable = override
        elif isinstance(command, (ast.List, ast.Tuple)) and command.elts:
            executable = command.elts[0]
        elif isinstance(command, ast.Constant) and isinstance(command.value, str):
            executable = command
        else:
            executable = None
        if not isinstance(executable, ast.Constant) or not isinstance(
            executable.value, str,
        ):
            if not dynamic_commands_allowed:
                raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_COMMAND")
            continue
        token = executable.value.strip()
        if not token or any(char in token for char in "$`\0"):
            if not dynamic_commands_allowed:
                raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_COMMAND")
            continue
        bind_executable(dependencies, token)
    return frozenset(dependencies)
