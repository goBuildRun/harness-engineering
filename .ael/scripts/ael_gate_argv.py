#!/usr/bin/env python3
"""Static top-level argv context for gate dependency discovery."""
from __future__ import annotations

from dataclasses import dataclass
import os
import shlex
import shutil
from pathlib import Path

from ael_gate_language import (
    SHELL_ASSIGNMENT, SHELL_CODE_LOADERS, absolute_search_path, analyze_shell,
    check_language_source, python_argv_modules, shell_command_indexes,
    shell_position_guarded, shell_words,
)
from ael_gate_imports import python_module_executes
from ael_gate_manifest import GateInputError


@dataclass(frozen=True)
class CommandContext:
    argv: tuple[str, ...]
    cwd: Path
    path_value: str
    pythonpath_value: str

    @property
    def analysis_context(self) -> tuple[Path, str, str]:
        return self.cwd, self.path_value, self.pythonpath_value


@dataclass(frozen=True)
class InlinePath:
    path: Path
    context: tuple[Path, str, str]
    analyze: bool = False
    sourced: bool = False
    guaranteed: bool = True


@dataclass(frozen=True)
class InlineBindings:
    paths: tuple[InlinePath, ...]
    modules: tuple[
        tuple[str, tuple[Path, str, str], bool, Path | None, frozenset[str]], ...
    ]


def _env_split(arguments: list[str]) -> list[str]:
    if arguments[:1] != ["-S"]:
        return arguments
    if len(arguments) < 2:
        raise GateInputError("GATE_INPUT_INTERPRETER_NOT_FOUND")
    try:
        return [*shlex.split(arguments[1]), *arguments[2:]]
    except ValueError as exc:
        raise GateInputError("GATE_INPUT_SHELL_PARSE_FAILED") from exc


def command_context(command: list[str], execution_cwd: Path) -> CommandContext:
    """Resolve static ``env`` assignments/chdir before interpreting its command."""
    argv = [os.fspath(item) for item in command]
    cwd = execution_cwd.resolve()
    path_value = os.environ.get("PATH", "")
    pythonpath_value = os.environ.get("PYTHONPATH", "")
    if not argv or Path(argv[0]).name != "env":
        return CommandContext(tuple(argv), cwd, path_value, pythonpath_value)

    arguments = _env_split(argv[1:])
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item == "--":
            index += 1
            break
        if item in {"-C", "--chdir"}:
            if index + 1 >= len(arguments):
                raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_CONTEXT")
            target = Path(arguments[index + 1]).expanduser()
            cwd = (target if target.is_absolute() else cwd / target).resolve(strict=False)
            index += 2
            continue
        if item.startswith("--chdir="):
            target = Path(item.split("=", 1)[1]).expanduser()
            cwd = (target if target.is_absolute() else cwd / target).resolve(strict=False)
            index += 1
            continue
        if item in {"-u", "--unset"}:
            if index + 1 >= len(arguments):
                raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_CONTEXT")
            name = arguments[index + 1]
            if name == "PATH":
                path_value = ""
            elif name == "PYTHONPATH":
                pythonpath_value = ""
            index += 2
            continue
        if item.startswith("--unset="):
            name = item.split("=", 1)[1]
            if name == "PATH":
                path_value = ""
            elif name == "PYTHONPATH":
                pythonpath_value = ""
            index += 1
            continue
        if SHELL_ASSIGNMENT.fullmatch(item):
            name, value = item.split("=", 1)
            if name == "PATH":
                path_value = value
            elif name == "PYTHONPATH":
                pythonpath_value = value
            index += 1
            continue
        if item.startswith("-"):
            index += 1
            continue
        break
    return CommandContext(
        tuple(arguments[index:]), cwd, path_value, pythonpath_value,
    )


def _resolved_command(token: str, cwd: Path, search_paths: tuple[str, ...]) -> Path:
    if "/" in token or token.startswith("~"):
        raw = Path(token).expanduser()
        return (raw if raw.is_absolute() else cwd / raw).resolve(strict=False)
    found = next((found for value in search_paths if (
        found := shutil.which(token, path=value)
    )), None)
    if not found:
        raise GateInputError("GATE_INPUT_EXECUTABLE_NOT_FOUND")
    return Path(found).resolve(strict=False)


def loader_target(arguments: tuple[str, ...], loader: str) -> str:
    option_values = (
        {"-W", "-X", "--check-hash-based-pycs"}
        if loader.startswith("python")
        else {"-o", "+o", "-O", "+O", "--init-file", "--rcfile"}
    )
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            index += 1
            break
        if argument in option_values:
            index += 2
            continue
        if argument.startswith(("-", "+")) or SHELL_ASSIGNMENT.fullmatch(argument):
            index += 1
            continue
        return argument
    return arguments[index] if index < len(arguments) else ""


def sourced_context_mutation(
    words: list[str], cwd: Path | None = None, path_value: str = "",
) -> bool:
    """Detect caller-visible source mutations while ignoring subshell-local state."""
    command_indexes = shell_command_indexes(words)
    depth = 0
    for index, word in enumerate(words):
        if word == "(":
            depth += 1
            continue
        if word == ")":
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        if SHELL_ASSIGNMENT.fullmatch(word):
            variable = word.split("=", 1)[0].removesuffix("+")
            if variable in {"PATH", "PYTHONPATH"}:
                return True
        if index not in command_indexes:
            continue
        loader = Path(word).name
        if loader in {"cd", "popd", "pushd"}:
            return True
        arguments = []
        for argument in words[index + 1:]:
            if argument in {";", "&", "&&", "|", "||", "(", ")", "\n"}:
                break
            arguments.append(argument)
        if loader == "unset" and {"PATH", "PYTHONPATH"}.intersection(arguments):
            return True
        if loader == "read" and {"PATH", "PYTHONPATH"}.intersection(arguments):
            return True
        if loader == "printf" and "-v" in arguments:
            target = arguments[arguments.index("-v") + 1:][:1]
            if target and target[0] in {"PATH", "PYTHONPATH"}:
                return True
        if loader in {"alias", "eval", "exec", "hash", "unalias"}:
            return True
        if loader in SHELL_CODE_LOADERS or "/" in word or word.startswith("~"):
            continue
        if cwd is not None and shutil.which(word, path=absolute_search_path(path_value, cwd)):
            continue
        if loader not in {":", "break", "continue", "echo", "false", "return", "true"}:
            return True
    return False


def inline_shell_dependencies(
    context: CommandContext, *, fail_on_python_parse: bool = False,
    allow_dynamic_python: bool = False, on_dynamic_import=None,
    check_deadline=None, depth: int = 0, parent_guarded: bool = False,
) -> InlineBindings:
    """Resolve executable bytes selected by a top-level shell ``-c`` body."""
    empty = InlineBindings((), ())
    if not context.argv or Path(context.argv[0]).name not in SHELL_CODE_LOADERS:
        return empty
    loader = Path(context.argv[0]).name
    if loader not in {"bash", "dash", "ksh", "sh", "zsh"} or "-c" not in context.argv:
        return empty
    if depth >= 8:
        raise GateInputError("GATE_INPUT_LIMIT_EXCEEDED")
    code_index = context.argv.index("-c") + 1
    code = context.argv[code_index] if code_index < len(context.argv) else ""
    if not code:
        raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND")
    check_language_source(code)
    words = shell_words(code, check_deadline)
    commands, _roots = analyze_shell(
        words, context.cwd, context.path_value, context.pythonpath_value,
    )
    dependencies: list[InlinePath] = []
    modules: list[
        tuple[str, tuple[Path, str, str], bool, Path | None, frozenset[str]]
    ] = []
    for command in commands:
        token = command.token
        if Path(token).name in {"cd", "export", "readonly", "set", "unset"}:
            continue
        command_context_value = (
            command.cwd, command.path_value, command.pythonpath_value,
        )
        guarded = parent_guarded or shell_position_guarded(words, command.index)
        command_loader = token if token == "." else Path(token).name
        python_interpreter = None
        if command_loader.startswith("python"):
            raw_interpreter = Path(token).expanduser()
            python_interpreter = (
                raw_interpreter if raw_interpreter.is_absolute()
                else command.cwd / raw_interpreter if "/" in token
                else _resolved_command(token, command.cwd, command.search_paths)
            )
        python_options = frozenset(
            argument for argument in command.arguments
            if argument in {"-E", "-I", "-P", "-S"}
        )
        if command_loader not in {".", "source"}:
            dependencies.append(InlinePath(
                _resolved_command(token, command.cwd, command.search_paths),
                command_context_value, guaranteed=not guarded,
            ))
        if command_loader not in SHELL_CODE_LOADERS and not command_loader.startswith("python"):
            continue
        arguments = command.arguments
        if command_loader == "eval":
            raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND")
        if "-c" in arguments:
            if command_loader.startswith("python"):
                modules.extend(
                    (
                        module, command_context_value, not guarded,
                        python_interpreter, python_options,
                    )
                    for module in python_argv_modules(
                        [token, *arguments], fail_on_parse=fail_on_python_parse,
                        allow_dynamic=allow_dynamic_python,
                        on_dynamic=on_dynamic_import, check_deadline=check_deadline,
                    )
                )
                continue
            nested = inline_shell_dependencies(
                CommandContext(
                    (token, *arguments), command.cwd,
                    command.path_value, command.pythonpath_value,
                ),
                fail_on_python_parse=fail_on_python_parse,
                allow_dynamic_python=allow_dynamic_python,
                on_dynamic_import=on_dynamic_import, check_deadline=check_deadline,
                depth=depth + 1, parent_guarded=guarded,
            )
            dependencies.extend(nested.paths)
            modules.extend(nested.modules)
            continue
        if command_loader.startswith("python") and "-m" in arguments:
            modules.extend(
                (
                    module, command_context_value, not guarded,
                    python_interpreter, python_options,
                )
                for module in python_argv_modules(
                    [token, *arguments], fail_on_parse=fail_on_python_parse,
                    allow_dynamic=allow_dynamic_python,
                    on_dynamic=on_dynamic_import, check_deadline=check_deadline,
                )
            )
            continue
        target = loader_target(arguments, command_loader)
        if not target:
            continue
        if any(char in target for char in "$`*?["):
            raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND")
        if command_loader in {".", "source"} and "/" not in target:
            target_path = _resolved_command(target, command.cwd, command.search_paths)
        else:
            raw = Path(target).expanduser()
            target_path = raw if raw.is_absolute() else command.cwd / raw
        dependencies.append(InlinePath(
            target_path.resolve(strict=False), command_context_value,
            analyze=True, sourced=command_loader in {".", "source"},
            guaranteed=not guarded,
        ))
    return InlineBindings(tuple(dependencies), tuple(modules))


def command_executes_path(command: list[str], execution_cwd: Path, target: Path) -> bool:
    """Return true only when static argv semantics place target on an execution path."""
    context = command_context(command, execution_cwd)
    if not context.argv:
        return False
    target = target.resolve(strict=False)
    search_path = (absolute_search_path(context.path_value, context.cwd),)
    try:
        executable = _resolved_command(context.argv[0], context.cwd, search_path)
    except GateInputError:
        return False
    if executable == target:
        return True
    loader = Path(context.argv[0]).name
    if loader in {"bash", "dash", "ksh", "sh", "zsh"} and "-c" in context.argv:
        bindings = inline_shell_dependencies(context, fail_on_python_parse=True)
        if any(
            binding.path == target and binding.guaranteed for binding in bindings.paths
        ):
            return True
        for module, module_context, guaranteed, interpreter, options in bindings.modules:
            if not guaranteed:
                continue
            cwd, _path_value, pythonpath_value = module_context
            if python_module_executes(
                module, target, cwd, pythonpath_value, interpreter, options,
            ):
                return True
        return False
    if loader not in SHELL_CODE_LOADERS and not loader.startswith("python"):
        return False
    if loader == "eval":
        return False
    if "-c" in context.argv:
        return False
    if loader.startswith("python") and "-m" in context.argv:
        module_index = context.argv.index("-m") + 1
        module = context.argv[module_index] if module_index < len(context.argv) else ""
        lexical = Path(context.argv[0]).expanduser()
        lexical = lexical if lexical.is_absolute() else context.cwd / lexical
        return python_module_executes(
            module, target, context.cwd, context.pythonpath_value, lexical,
            frozenset(
                argument for argument in context.argv
                if argument in {"-E", "-I", "-P", "-S"}
            ),
        )
    script = loader_target(context.argv[1:], loader)
    if not script or any(char in script for char in "$`*?["):
        return False
    raw = Path(script).expanduser()
    resolved = raw if raw.is_absolute() else context.cwd / raw
    return resolved.resolve(strict=False) == target
