#!/usr/bin/env python3
"""Run a test command without shell eval and emit Harness JSON."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from harness_output import dump_json
from process_control import run_process_group

SHELL_CONTROL = re.compile(r"[;&|><`\n\r]")
SHELLS = {"bash", "dash", "fish", "sh", "zsh"}


def emit(decision: str, reason: str, **extra) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def parse_argv(raw_args: list[str]) -> tuple[list[str], str | None]:
    if not raw_args:
        return [], "ERROR: 必须传入要在沙箱中执行的命令"

    if len(raw_args) == 1:
        command = raw_args[0].strip()
        if SHELL_CONTROL.search(command):
            return [], "VIOLATION_SANDBOX: 禁止 shell 控制符，请传入单一测试命令与参数"
        try:
            return shlex.split(command), None
        except ValueError as exc:
            return [], f"VIOLATION_SANDBOX: 命令解析失败: {exc}"

    joined = " ".join(raw_args)
    if SHELL_CONTROL.search(joined):
        return [], "VIOLATION_SANDBOX: 禁止 shell 控制符，请传入单一测试命令与参数"
    return raw_args, None


def reject(argv: list[str]) -> str | None:
    if not argv:
        return "ERROR: 命令为空"

    exe = os.path.basename(argv[0])
    if exe in {"sudo", "su"}:
        return "VIOLATION_SANDBOX: 禁止提权命令"

    if exe == "rm" and "-rf" in argv and any(arg in {"/", "/*"} for arg in argv):
        return "VIOLATION_SANDBOX: 危险删除指令已拦截"

    if exe in SHELLS and "-c" in argv:
        return "VIOLATION_SANDBOX: 禁止通过 shell -c 绕过 argv 执行"

    return None


def run_controlled(argv: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
    print(f"[ControlledExec] cwd={cwd} 执行: {shlex.join(argv)}", file=sys.stderr)
    try:
        proc = run_process_group(argv, cwd=cwd, timeout=timeout)
    except FileNotFoundError:
        return False, f"CONTROLLED_EXEC_FAILED: command_not_found={argv[0]}"
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "")[-2000:].replace("\n", " ")
        return False, f"CONTROLLED_EXEC_TIMEOUT: timeout={timeout}s log={output}"
    if proc.returncode != 0:
        cleaned = (proc.stdout or "").splitlines()[-25:]
        log = " ".join(cleaned)[:2000]
        return False, f"CONTROLLED_EXEC_FAILED: exit={proc.returncode} log={log}"
    return True, "CONTROLLED_EXEC_SUCCESS: argv 受控命令执行成功（非容器隔离）"


def docker_command(argv: list[str], cwd: Path) -> list[str]:
    image = os.environ.get("HARNESS_SANDBOX_IMAGE") or "python:3.11-slim"
    network = os.environ.get("HARNESS_SANDBOX_DOCKER_NETWORK") or "none"
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--workdir",
        "/workspace",
        "-v",
        f"{cwd}:/workspace",
        image,
        *argv,
    ]


def run_docker(argv: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
    docker_argv = docker_command(argv, cwd)
    image = docker_argv[9]
    network = docker_argv[4]
    print(f"[DockerSandbox] image={image} network={network} cwd={cwd} 执行: {shlex.join(argv)}", file=sys.stderr)
    try:
        proc = run_process_group(docker_argv, timeout=timeout)
    except FileNotFoundError:
        return False, "DOCKER_SANDBOX_UNAVAILABLE: command_not_found=docker"
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "")[-2000:].replace("\n", " ")
        return False, f"DOCKER_SANDBOX_TIMEOUT: timeout={timeout}s log={output}"
    if proc.returncode != 0:
        cleaned = (proc.stdout or "").splitlines()[-25:]
        log = " ".join(cleaned)[:2000]
        return False, f"DOCKER_SANDBOX_FAILED: exit={proc.returncode} log={log}"
    return True, "DOCKER_SANDBOX_SUCCESS: 命令已在 Docker 容器内执行"


def run_remote(argv: list[str], timeout: int) -> tuple[bool, str]:
    endpoint = os.environ.get("HARNESS_SANDBOX_REMOTE_URL", "").strip()
    workspace_ref = os.environ.get("HARNESS_SANDBOX_WORKSPACE_REF", "").strip()
    token = os.environ.get("HARNESS_SANDBOX_REMOTE_TOKEN", "").strip()
    subject = os.environ.get("HARNESS_SANDBOX_SUBJECT_DIGEST", "").strip()
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        return False, "REMOTE_SANDBOX_CONFIG_INVALID: HARNESS_SANDBOX_REMOTE_URL must use https"
    if not workspace_ref or not token:
        return False, "REMOTE_SANDBOX_CONFIG_MISSING: workspace ref and remote token are required"
    payload = json.dumps({
        "schema_version": 1,
        "workspace_ref": workspace_ref,
        "subject_digest": subject,
        "argv": argv,
        "timeout_seconds": timeout,
        "network": "none",
    }).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout + 30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return False, f"REMOTE_SANDBOX_HTTP_ERROR: status={exc.code}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, f"REMOTE_SANDBOX_FAILED: {type(exc).__name__}"
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        return False, "REMOTE_SANDBOX_RESPONSE_INVALID"
    if subject and result.get("subject_digest") != subject:
        return False, "REMOTE_SANDBOX_SUBJECT_MISMATCH"
    reason = str(result.get("reason") or "REMOTE_SANDBOX_NO_REASON")[:2000]
    return result.get("decision") == "pass", reason


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument(
        "--backend", choices=("controlled", "docker", "remote"),
        default=os.environ.get("HARNESS_SANDBOX_BACKEND") or "controlled",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    raw_command = args.command
    if raw_command and raw_command[0] == "--":
        raw_command = raw_command[1:]
    argv, error = parse_argv(raw_command)
    if error:
        emit("block", error)
        return 0

    violation = reject(argv)
    if violation:
        emit("block", violation)
        return 0

    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        emit("block", f"SANDBOX_CWD_NOT_FOUND: {cwd}")
        return 0
    timeout = int(os.environ.get("HARNESS_SANDBOX_TIMEOUT_SECONDS") or "600")
    if args.backend == "docker":
        ok, reason = run_docker(argv, cwd, timeout)
    elif args.backend == "remote":
        ok, reason = run_remote(argv, timeout)
    else:
        ok, reason = run_controlled(argv, cwd, timeout)
    emit("pass" if ok else "block", reason, backend=args.backend, cwd=str(cwd), argv=argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
