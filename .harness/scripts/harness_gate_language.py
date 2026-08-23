#!/usr/bin/env python3
"""Static shell and Python language helpers for gate dependency discovery."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import os
import re
import shlex
import shutil
from pathlib import Path

from harness_gate_imports import python_modules
from harness_gate_manifest import GateInputError


SHELL_TOOL_REF = re.compile(r"[A-Za-z0-9_.-]+\.(?:py|sh)")
SHELL_PATH_REF = re.compile(r"(?<![A-Za-z0-9_.-])((?:\./|\.\./|/)[A-Za-z0-9_./-]+)")
SHELL_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=.*")
SHELL_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
SHELL_SEPARATORS = {";", "&", "&&", "|", "||", "(", ")", "\n"}
SHELL_COMMAND_PREFIXES = {"command", "exec", "env", "nohup"}
SHELL_CODE_LOADERS = {
    ".", "bash", "dash", "eval", "ksh", "python", "python3", "sh", "source", "zsh",
}
SHELL_CONTROL_WORDS = {
    "!", "{", "}", "do", "elif", "else", "if", "then", "time", "until", "while",
}
MAX_LANGUAGE_SOURCE_CHARS = 4 * 1024 * 1024


def check_language_source(value: str) -> None:
    if len(value) > MAX_LANGUAGE_SOURCE_CHARS:
        raise GateInputError("GATE_INPUT_LIMIT_EXCEEDED")


def env_command(arguments: list[str]) -> str:
    args = list(arguments)
    if args[:1] == ["-S"]:
        args = args[1:]
    while args:
        item = args.pop(0)
        if item == "--":
            return args[0] if args else ""
        if item in {"-u", "--unset", "-C", "--chdir"}:
            if not args:
                return ""
            args.pop(0)
            continue
        if item.startswith(("--unset=", "--chdir=")):
            continue
        if item.startswith("-") or "=" in item:
            continue
        return item
    return ""


def python_argv_modules(
    command: list[str], *, fail_on_parse: bool = False, allow_dynamic: bool = False,
    on_dynamic=None, check_deadline=None,
) -> set[str]:
    argv = list(command)
    if argv and Path(argv[0]).name == "env":
        selected = env_command(argv[1:])
        index = next((i for i, item in enumerate(argv[1:], 1) if item == selected), -1)
        argv = argv[index:] if index > 0 else []
    if not argv or "python" not in Path(argv[0]).name.lower():
        return set()
    arguments = argv[1:]
    if "-m" in arguments:
        index = arguments.index("-m") + 1
        module = arguments[index] if index < len(arguments) else ""
        if not module or any(char in module for char in "$`"):
            raise GateInputError("GATE_INPUT_DYNAMIC_PYTHON_IMPORT")
        return {module}
    if "-c" in arguments:
        index = arguments.index("-c") + 1
        code = arguments[index] if index < len(arguments) else ""
        check_language_source(code)
        if check_deadline is not None:
            check_deadline()
        try:
            tree = ast.parse(code)
        except (MemoryError, RecursionError, SyntaxError, ValueError) as exc:
            if fail_on_parse:
                raise GateInputError("GATE_INPUT_PYTHON_PARSE_FAILED") from exc
            if on_dynamic is not None:
                on_dynamic()
            return set()
        if check_deadline is not None:
            check_deadline()
        return python_modules(
            tree, allow_manifest_bound_dynamic=allow_dynamic,
            check_deadline=check_deadline, on_dynamic=on_dynamic,
        )
    return set()


def _without_heredoc_bodies(shell_body: str) -> str:
    rendered: list[str] = []
    delimiter = ""
    for line in shell_body.splitlines(keepends=True):
        if delimiter:
            if line.strip() == delimiter:
                delimiter = ""
            rendered.append("\n" if line.endswith("\n") else "")
            continue
        rendered.append(line)
        match = SHELL_HEREDOC.search(line)
        if match:
            delimiter = match.group(2)
    return "".join(rendered)


def _punctuation_tokens(token: str) -> list[str]:
    punctuation = ";&|()<>\n"
    if not token or any(char not in punctuation for char in token):
        return [token]
    rendered: list[str] = []
    index = 0
    while index < len(token):
        pair = token[index:index + 2]
        if pair in {"&&", "||", "<<", ">>", ";;"}:
            rendered.append(pair)
            index += 2
        else:
            rendered.append(token[index])
            index += 1
    return rendered


def shell_words(shell_body: str, check_deadline=None) -> list[str]:
    check_language_source(shell_body)
    try:
        lexer = shlex.shlex(
            _without_heredoc_bodies(shell_body).replace("\\\n", " "),
            posix=True,
            punctuation_chars=";&|()<>\n",
        )
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = "#"
        words: list[str] = []
        for token in lexer:
            if check_deadline is not None:
                check_deadline()
            words.extend(_punctuation_tokens(token))
        if check_deadline is not None:
            check_deadline()
        return words
    except ValueError as exc:
        raise GateInputError("GATE_INPUT_SHELL_PARSE_FAILED") from exc


def shell_command_indexes(words: list[str]) -> set[int]:
    indexes: set[int] = set()
    array_indexes: set[int] = set()
    for index, word in enumerate(words[:-1]):
        if not SHELL_ASSIGNMENT.fullmatch(word) or not word.endswith("="):
            continue
        if words[index + 1] != "(":
            continue
        depth = 0
        for candidate in range(index + 1, len(words)):
            array_indexes.add(candidate)
            if words[candidate] == "(":
                depth += 1
            elif words[candidate] == ")":
                depth -= 1
                if depth == 0:
                    break
    expect_command = True
    skip_redirection_target = False
    test_closer = ""
    for index, word in enumerate(words):
        if index in array_indexes:
            continue
        if test_closer:
            if word == test_closer:
                test_closer = ""
                expect_command = False
            continue
        if word in {"[[", "["} and expect_command:
            test_closer = "]]" if word == "[[" else "]"
            continue
        if word == "$" and index + 1 < len(words) and words[index + 1] == "(":
            continue
        if word in SHELL_SEPARATORS:
            expect_command = True
            skip_redirection_target = False
            continue
        if word and set(word) <= {"<", ">"}:
            skip_redirection_target = True
            continue
        if skip_redirection_target:
            skip_redirection_target = False
            continue
        if not expect_command:
            continue
        if SHELL_ASSIGNMENT.fullmatch(word) or word.startswith("-"):
            continue
        if word in SHELL_CONTROL_WORDS or word in SHELL_COMMAND_PREFIXES:
            continue
        indexes.add(index)
        expect_command = False
    return indexes


def shell_position_guarded(words: list[str], index: int) -> bool:
    blocks: list[bool] = []
    function_blocks: list[bool] = []
    guarded_next = False
    terminated = False
    command_indexes = shell_command_indexes(words)
    for position, word in enumerate(words[:index]):
        if word == "{":
            function_start = (
                position >= 3 and words[position - 2:position] == ["(", ")"]
                or position >= 2 and words[position - 2] == "function"
            )
            function_blocks.append(function_start or any(function_blocks))
        elif word == "}" and function_blocks:
            function_blocks.pop()
        if word in {"if", "while", "until", "for", "case"}:
            blocks.append(False)
        elif word in {"then", "elif", "else", "do"} and blocks:
            blocks[-1] = True
        elif word in {"fi", "done", "esac"} and blocks:
            blocks.pop()
        if word in {"&&", "||"}:
            guarded_next = True
        elif word in {";", "\n"}:
            guarded_next = False
        if position in command_indexes and (
            Path(word).name in {"exit", "return"}
            or position > 0 and words[position - 1] == "exec"
        ):
            terminated = True
    return terminated or guarded_next or any(blocks) or any(function_blocks)


@dataclass(frozen=True)
class ShellCommand:
    index: int
    token: str
    arguments: tuple[str, ...]
    cwd: Path
    cwd_candidates: tuple[Path, ...]
    search_paths: tuple[str, ...]
    path_value: str
    pythonpath_value: str


def _expanded_path_value(value: str, current: str, variable: str) -> str:
    expanded = value.replace(f"${variable}", current).replace(f"${{{variable}}}", current)
    if any(char in expanded for char in "$`"):
        raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_CONTEXT")
    return expanded


def absolute_search_path(value: str, cwd: Path) -> str:
    return os.pathsep.join(
        str(
            Path(item).expanduser() if Path(item).is_absolute()
            else (cwd / item).resolve(strict=False)
        )
        for item in value.split(os.pathsep) if item
    )


def manifest_script_path(value: str, script: Path) -> Path | None:
    for variable in ("SCRIPT_DIR", "HARNESS_OUTPUT_SCRIPT_DIR"):
        for prefix in (f"${variable}/", f"${{{variable}}}/"):
            if value.startswith(prefix):
                return script.parent / value.removeprefix(prefix)
    return None


def command_path(
    item: str, index: int, execution_cwd: Path, search_path: str,
) -> tuple[Path | None, str]:
    path = Path(item)
    if index == 0 and not path.is_absolute() and not {"/", "\\"}.intersection(item):
        found = shutil.which(item, path=search_path)
        return (Path(found), f"executable:{item}") if found else (
            None, f"executable:{item}",
        )
    candidate = path if path.is_absolute() else execution_cwd / path
    if candidate.is_file() or path.is_absolute() or {"/", "\\"}.intersection(item):
        return candidate, ""
    return None, ""


def analyze_shell(
    words: list[str], execution_cwd: Path, initial_path: str,
    initial_pythonpath: str, *, allow_manifest_bound_dynamic: bool = False,
) -> tuple[list[ShellCommand], list[Path]]:
    indexes = shell_command_indexes(words)
    cwd = execution_cwd.resolve()
    cwd_candidates = [cwd]
    current_path = initial_path
    current_pythonpath = initial_pythonpath
    python_roots: list[Path] = []
    commands: list[ShellCommand] = []
    subshells: list[tuple[Path, str, str]] = []
    for index, word in enumerate(words):
        if word == "(":
            subshells.append((cwd, current_path, current_pythonpath))
            continue
        if word == ")" and subshells:
            cwd, current_path, current_pythonpath = subshells.pop()
            continue
        if SHELL_ASSIGNMENT.fullmatch(word):
            variable, value = word.split("=", 1)
            if variable == "PATH":
                try:
                    expanded = _expanded_path_value(value, current_path, "PATH")
                except GateInputError:
                    if allow_manifest_bound_dynamic:
                        continue
                    raise
                current_path = expanded
            elif variable == "PYTHONPATH":
                try:
                    current_pythonpath = _expanded_path_value(
                        value, current_pythonpath, "PYTHONPATH",
                    )
                except GateInputError:
                    if allow_manifest_bound_dynamic:
                        continue
                    raise
        if index not in indexes:
            continue
        if any(char in word for char in "$`"):
            if allow_manifest_bound_dynamic:
                continue
            raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_COMMAND")
        arguments: list[str] = []
        for argument in words[index + 1:]:
            if argument in SHELL_SEPARATORS:
                break
            arguments.append(argument)
        command = ShellCommand(
            index=index, token=word, arguments=tuple(arguments), cwd=cwd,
            cwd_candidates=tuple(cwd_candidates),
            search_paths=(absolute_search_path(current_path, cwd),),
            path_value=current_path, pythonpath_value=current_pythonpath,
        )
        commands.append(command)
        for item in current_pythonpath.split(os.pathsep):
            if not item:
                continue
            root = Path(item).expanduser()
            root = root if root.is_absolute() else cwd / root
            resolved = root.resolve(strict=False)
            if resolved not in python_roots:
                python_roots.append(resolved)
        if word == "cd" or Path(word).name == "cd":
            targets = [item for item in arguments if not item.startswith("-")]
            if not targets or any(char in targets[0] for char in "$`*?["):
                if allow_manifest_bound_dynamic:
                    continue
                raise GateInputError("GATE_INPUT_DYNAMIC_SHELL_CONTEXT")
            target = Path(targets[0]).expanduser()
            cwd = (target if target.is_absolute() else cwd / target).resolve(strict=False)
            if cwd not in cwd_candidates:
                cwd_candidates.append(cwd)
    return commands, python_roots
