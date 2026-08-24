#!/usr/bin/env python3
"""Bounded executable and imported-tool dependency digests for AEL gates."""
from __future__ import annotations

import ast
import os
import re
import shlex
import shutil
from pathlib import Path

from ael_gate_dependency_io import (
    dependency_entries, dependency_text, external_dependency_digest,
)
from ael_gate_imports import python_modules
from ael_gate_argv import (
    command_context, inline_shell_dependencies, loader_target, sourced_context_mutation,
)
from ael_gate_language import (
    SHELL_ASSIGNMENT, SHELL_CODE_LOADERS, SHELL_SEPARATORS, SHELL_TOOL_REF,
    absolute_search_path, analyze_shell, command_path,
    env_command, manifest_script_path, python_argv_modules, shell_words,
)
from ael_gate_manifest import CandidateManifest, GateInputError
from ael_gate_python import (
    PythonEnvironment, bind_path_executable, source_dependencies,
)
def tool_dependency_entries(
    harness: Path, command: list[str], candidates_manifest: CandidateManifest, *,
    execution_cwd: Path | None = None, bind_interpreter_environment: bool = False,
    strict_external_python: bool = False, dynamic_import_callback=None,
) -> list[tuple[str, str]]:
    harness = harness.resolve()
    execution_cwd = (execution_cwd or harness).resolve()
    scripts = (harness / ".ael/scripts").resolve()
    product = candidates_manifest.product.resolve()
    argv_context = command_context(command, execution_cwd)
    initial_context = argv_context.analysis_context
    pending: list[tuple[Path, tuple[Path, str, str], bool]] = []
    direct: dict[str, str] = {}
    def enqueue(
        path: Path, context: tuple[Path, str, str] = initial_context, *, sourced: bool = False,
    ) -> None:
        pending.append((path.resolve(), context, sourced))
    def record(
        path: Path, *, label: str = "", context: tuple[Path, str, str] = initial_context,
        force_analyze: bool = False, sourced: bool = False,
    ) -> None:
        lexical = path.parent.resolve(strict=False) / path.name
        resolved = path.resolve(strict=False)
        if lexical.is_relative_to(harness):
            candidates_manifest.digest(path, root=harness)
        elif lexical.is_relative_to(candidates_manifest.product):
            candidates_manifest.digest(path, root=candidates_manifest.product)
        product = candidates_manifest.product.resolve()
        if force_analyze or lexical.is_relative_to(scripts) or (
            lexical.is_relative_to(product)
            and lexical.is_file()
            and (lexical.suffix in {".py", ".sh"} or os.access(lexical, os.X_OK))
        ):
            enqueue(resolved, context, sourced=sourced)
        elif lexical.is_relative_to(harness):
            rel = str(lexical.relative_to(harness))
            direct[rel] = candidates_manifest.digest(path, root=harness)
        elif lexical.is_relative_to(candidates_manifest.product):
            rel = f"product:{lexical.relative_to(candidates_manifest.product)}"
            direct[rel] = candidates_manifest.digest(
                path, root=candidates_manifest.product,
            )
        else:
            direct[label or f"external:{resolved}"] = external_dependency_digest(
                resolved, candidates_manifest,
            )

    environment = PythonEnvironment(
        harness, product, argv_context.cwd, candidates_manifest, record, scripts,
    )
    python_search_roots = environment.roots
    bind_import = environment.bind_import
    add_python_root = environment.add_root
    add_interpreter_roots = environment.add_interpreter
    for item in argv_context.pythonpath_value.split(os.pathsep):
        if item:
            add_python_root(
                Path(item) if Path(item).is_absolute() else argv_context.cwd / item,
            )
    search_path = absolute_search_path(argv_context.path_value, argv_context.cwd)
    if command and Path(command[0]).name == "env":
        env_path, env_label = command_path(command[0], 0, execution_cwd, absolute_search_path(
            os.environ.get("PATH", ""), execution_cwd,
        ))
        if env_path is not None:
            record(env_path, label=env_label)
    for index, item in enumerate(argv_context.argv):
        try:
            item = os.fspath(item)
        except TypeError:
            continue
        if not isinstance(item, str):
            continue
        path, label = command_path(item, index, argv_context.cwd, search_path)
        if path is not None:
            record(path, label=label)
            if bind_interpreter_environment and index == 0 and "python" in path.name.lower():
                add_interpreter_roots(path)
        elif label:
            direct[label] = "absent"

    inline_bindings = inline_shell_dependencies(
        argv_context, fail_on_python_parse=strict_external_python,
        allow_dynamic_python=not strict_external_python,
        on_dynamic_import=dynamic_import_callback,
        check_deadline=candidates_manifest.check_deadline,
    )
    for dependency in inline_bindings.paths:
        record(
            dependency.path, context=dependency.context,
            force_analyze=dependency.analyze, sourced=dependency.sourced,
        )

    argv_dependencies: set[Path] = set()
    environment.bind_context_imports(argv_dependencies, inline_bindings.modules)
    for module in python_argv_modules(
        list(argv_context.argv), fail_on_parse=strict_external_python,
        allow_dynamic=not strict_external_python,
        on_dynamic=dynamic_import_callback, check_deadline=candidates_manifest.check_deadline,
    ):
        bind_import(argv_dependencies, module, python_search_roots)
    pending.extend(
        (path.resolve(), initial_context, False)
        for path in argv_dependencies if path.is_file()
    )

    enqueued_pth_modules: set[str] = set()
    seen: set[Path] = set()
    analyzed: set[tuple[Path, Path, str, str, bool]] = set()
    while pending or environment.import_modules - enqueued_pth_modules:
        new_pth_modules = environment.import_modules - enqueued_pth_modules
        pth_dependencies: set[Path] = set()
        for module in sorted(new_pth_modules):
            bind_import(pth_dependencies, module, python_search_roots)
        enqueued_pth_modules.update(new_pth_modules)
        pending.extend(
            (dependency.resolve(), initial_context, False)
            for dependency in pth_dependencies if dependency.is_file()
        )
        if not pending:
            continue
        path, context, sourced = pending.pop()
        analysis_cwd, analysis_path, analysis_pythonpath = context
        analysis_key = (path, analysis_cwd, analysis_path, analysis_pythonpath, sourced)
        if analysis_key in analyzed:
            continue
        analyzed.add(analysis_key)
        seen.add(path)
        text = dependency_text(
            path, harness, candidates_manifest.product, candidates_manifest,
        )
        dependencies: set[Path] = set()
        dependency_contexts: dict[Path, set[tuple[Path, str, str]]] = {}
        sourced_contexts: set[tuple[Path, tuple[Path, str, str]]] = set()
        first_line = text.splitlines()[0] if text.startswith("#!") else ""
        python_script = path.suffix == ".py"
        if first_line:
            try:
                shebang = shlex.split(first_line[2:].strip())
            except ValueError:
                shebang = []
            if shebang:
                interpreter = Path(shebang[0])
                python_script = python_script or "python" in interpreter.name.lower()
                found = str(interpreter) if interpreter.is_absolute() else shutil.which(str(interpreter))
                if found:
                    if bind_interpreter_environment and "python" in Path(found).name.lower():
                        add_interpreter_roots(Path(found))
                    record(Path(found), label=f"interpreter:{interpreter}")
                else:
                    direct[f"interpreter:{interpreter}"] = "absent"
                if interpreter.name == "env" and len(shebang) > 1:
                    env_name = env_command(list(shebang[1:]))
                    python_script = python_script or "python" in Path(env_name).name
                    env_target = shutil.which(env_name, path=search_path) if env_name else None
                    if env_target:
                        if bind_interpreter_environment and "python" in Path(env_target).name.lower():
                            add_interpreter_roots(Path(env_target))
                        record(Path(env_target), label=f"interpreter:{env_name}")
                    else:
                        direct[f"interpreter:{env_name or 'missing'}"] = "absent"
        if python_script:
            roots = [path.parent, *python_search_roots]
            source_key = (
                "python-source", "\0".join([
                    str(path), str(strict_external_python), str(analysis_cwd),
                    analysis_path, analysis_pythonpath, *(str(root) for root in roots),
                ]),
            )
            dependencies.update(candidates_manifest.memoized(
                source_key,
                lambda: source_dependencies(
                    text, path, roots, bind_import,
                    allow_dynamic=not strict_external_python,
                    fail_on_parse=strict_external_python,
                    allow_dynamic_commands=not strict_external_python,
                    check_deadline=candidates_manifest.check_deadline,
                    on_dynamic_import=lambda: candidates_manifest.mark_dynamic_source(
                        source_key,
                    ),
                    bind_executable=lambda values, token: bind_path_executable(
                        values, token, cwd=analysis_cwd, path_value=analysis_path,
                        context=context, dependency_contexts=dependency_contexts,
                        strict=strict_external_python,
                    ),
                ),
            ))
            if (
                dynamic_import_callback is not None
                and candidates_manifest.source_has_dynamic_import(source_key)
            ):
                dynamic_import_callback()
        else:
            shell_body = "\n".join(text.splitlines()[1:]) if first_line else text
            for name in SHELL_TOOL_REF.findall(shell_body):
                dependencies.update((path.parent / name, scripts / name))
            words = shell_words(shell_body, candidates_manifest.check_deadline)
            if sourced and sourced_context_mutation(
                words, analysis_cwd, analysis_path,
            ):
                raise GateInputError("GATE_INPUT_SOURCED_CONTEXT_MUTATION")
            shell_commands, shell_python_roots = analyze_shell(
                words, analysis_cwd, analysis_path, analysis_pythonpath,
                allow_manifest_bound_dynamic=path.is_relative_to(harness),
            )
            for root in shell_python_roots:
                add_python_root(root)
            all_cwds = list(dict.fromkeys(
                [analysis_cwd]
                + [cwd for item in shell_commands for cwd in item.cwd_candidates]
            ))
            all_search_paths = list(dict.fromkeys(
                [search_path]
                + [value for item in shell_commands for value in item.search_paths]
            ))
            def add_static_path(
                value: str, command_context=None, *, sourced_target: bool = False,
            ) -> None:
                try:
                    candidate = Path(value).expanduser() if value.startswith("~") else Path(value)
                except RuntimeError as exc:
                    raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND") from exc
                if candidate.is_absolute():
                    selected = [candidate]
                else:
                    selected = [
                        command_context.cwd / candidate
                    ] if command_context else [cwd / candidate for cwd in all_cwds]
                    dependencies.update(selected)
                if candidate.is_absolute():
                    dependencies.add(candidate)
                if command_context:
                    inherited = (
                        command_context.cwd, command_context.path_value,
                        command_context.pythonpath_value,
                    )
                    for item in selected:
                        resolved = item.resolve(strict=False)
                        dependency_contexts.setdefault(resolved, set()).add(inherited)
                        if sourced_target:
                            sourced_contexts.add((resolved, inherited))
            for shell_command in shell_commands:
                loader = (
                    shell_command.token if shell_command.token == "."
                    else Path(shell_command.token).name
                )
                if "/" in shell_command.token or shell_command.token.startswith("~"):
                    add_static_path(shell_command.token)
                else:
                    for value in shell_command.search_paths:
                        found = shutil.which(shell_command.token, path=value)
                        if found:
                            dependency = Path(found)
                            dependencies.add(dependency)
                            dependency_contexts.setdefault(
                                dependency.resolve(strict=False), set(),
                            ).add((
                                shell_command.cwd, shell_command.path_value,
                                shell_command.pythonpath_value,
                            ))
                if loader not in SHELL_CODE_LOADERS and not loader.startswith("python"):
                    continue
                arguments = list(shell_command.arguments)
                if loader == "eval":
                    raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND")
                if "-c" in arguments:
                    code_index = arguments.index("-c") + 1
                    code = arguments[code_index] if code_index < len(arguments) else ""
                    if not code or re.fullmatch(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|`.*`", code):
                        raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND")
                    if loader.startswith("python"):
                        candidates_manifest.check_deadline()
                        try:
                            inline_tree = ast.parse(code)
                        except (MemoryError, RecursionError, SyntaxError, ValueError) as exc:
                            if strict_external_python:
                                raise GateInputError("GATE_INPUT_PYTHON_PARSE_FAILED") from exc
                            if dynamic_import_callback is not None:
                                dynamic_import_callback()
                            inline_tree = None
                        candidates_manifest.check_deadline()
                        for module in python_modules(
                            inline_tree, allow_manifest_bound_dynamic=not strict_external_python,
                            check_deadline=candidates_manifest.check_deadline,
                            on_dynamic=dynamic_import_callback,
                        ) if inline_tree else set():
                            bind_import(
                                dependencies, module, [*all_cwds, *python_search_roots],
                            )
                        continue
                    raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND")
                if loader.startswith("python") and "-m" in arguments:
                    module_index = arguments.index("-m") + 1
                    module = arguments[module_index] if module_index < len(arguments) else ""
                    if not module or any(char in module for char in "$`"):
                        raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND")
                    bind_import(
                        dependencies, module, [*all_cwds, *python_search_roots],
                    )
                    continue
                if loader.startswith("python") and arguments[:1] == ["-"]:
                    continue
                target = loader_target(tuple(arguments), loader)
                if target:
                    if any(char in target for char in "$`*?["):
                        resolved = manifest_script_path(target, path)
                        if resolved:
                            dependencies.add(resolved)
                        elif not path.is_relative_to(harness):
                            raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND")
                        continue
                    add_static_path(
                        target, shell_command, sourced_target=loader in {".", "source"},
                    )
                    if "/" not in target and not target.startswith("~"):
                        for value in shell_command.search_paths:
                            found = shutil.which(target, path=value)
                            if found:
                                dependency = Path(found)
                                dependencies.add(dependency)
                                dependency_contexts.setdefault(
                                    dependency.resolve(strict=False), set(),
                                ).add((
                                    shell_command.cwd, shell_command.path_value,
                                    shell_command.pythonpath_value,
                                ))
            for name in words:
                if SHELL_ASSIGNMENT.fullmatch(name):
                    continue
                if name in SHELL_SEPARATORS:
                    continue
                if "/" in name or name.startswith("~"):
                    if any(char in name for char in "$`*?["):
                        continue
                    add_static_path(name)
                    continue
                dependencies.update(
                    Path(found) for value in all_search_paths
                    if (found := shutil.which(name, path=value))
                )
        for candidate in dependencies:
            python_file = candidate.with_suffix(".py") if not candidate.suffix else candidate
            package_file = candidate / "__init__.py"
            for dependency in (candidate, python_file, package_file):
                if dependency.is_file():
                    resolved_dependency = dependency.resolve()
                    if bind_interpreter_environment and (
                        "python" in resolved_dependency.name.lower()
                        and os.access(resolved_dependency, os.X_OK)
                        and resolved_dependency.suffix != ".py"
                    ):
                        add_interpreter_roots(resolved_dependency)
                    recursive = any(
                        resolved_dependency.is_relative_to(root) and root.name not in {"site-packages", "dist-packages"}
                        for root in python_search_roots
                    ) or resolved_dependency in dependency_contexts or (
                        resolved_dependency.suffix == ".py"
                    )
                    if recursive:
                        contexts = dependency_contexts.get(resolved_dependency) or {context}
                        for dependency_context in contexts:
                            enqueue(
                                resolved_dependency, dependency_context,
                                sourced=(resolved_dependency, dependency_context)
                                in sourced_contexts,
                            )
                    else:
                        record(resolved_dependency)
    return dependency_entries(seen, direct, harness, product, candidates_manifest)
