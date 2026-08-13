#!/usr/bin/env python3
"""Run optional live acceptance checks for the Docker sandbox backend."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from harness_output import dump_json
from sandbox_exec import docker_command


def run_probe(argv: list[str], cwd: Path, timeout: int, *, image: str = "") -> tuple[bool, str]:
    command = docker_command(argv, cwd)
    if image:
        command[9] = image
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return False, "docker command not found"
    except subprocess.TimeoutExpired:
        return False, f"probe timed out after {timeout}s"
    output = (result.stdout or "").strip()[-1000:]
    if result.returncode != 0:
        return False, f"exit={result.returncode} output={output}"
    return True, output


def accept(cwd: Path, timeout: int, *, runtime_suite: bool = False) -> dict:
    probes: list[dict] = []
    cases = [
        (
            "workspace-visible",
            ["python3", "-c", "from pathlib import Path; assert Path('/workspace/AGENTS.md').is_file()"],
            True,
        ),
        (
            "network-isolated",
            [
                "python3",
                "-c",
                "import socket; s=socket.socket(); s.settimeout(1); "
                "result=s.connect_ex(('1.1.1.1', 53)); assert result != 0, result",
            ],
            True,
        ),
        (
            "argv-preserved",
            ["python3", "-c", "import sys; assert sys.argv[1:] == ['value with spaces', '$literal']", "value with spaces", "$literal"],
            True,
        ),
    ]
    for name, argv, should_succeed in cases:
        succeeded, detail = run_probe(argv, cwd, timeout)
        passed = succeeded is should_succeed
        probes.append({"name": name, "decision": "pass" if passed else "block", "detail": detail})
    if runtime_suite:
        runtime_cases = [
            (
                "python-business-test", "python:3.11-slim",
                ["python3", "-c", "import unittest; "
                 "r=unittest.TestResult(); unittest.FunctionTestCase(lambda: None).run(r); "
                 "assert r.wasSuccessful()"],
            ),
            (
                "node-business-test", "node:22-slim",
                ["node", "-e", "const test=require('node:test'); "
                 "const assert=require('node:assert/strict'); test('business smoke',()=>assert.equal(2+2,4));"],
            ),
        ]
        for name, image, argv in runtime_cases:
            succeeded, detail = run_probe(argv, cwd, timeout, image=image)
            probes.append({
                "name": name, "image": image,
                "decision": "pass" if succeeded else "block", "detail": detail,
            })
    decision = "pass" if all(item["decision"] == "pass" for item in probes) else "block"
    return {
        "decision": decision,
        "reason": "DOCKER_SANDBOX_ACCEPTED" if decision == "pass" else "DOCKER_SANDBOX_ACCEPTANCE_FAILED",
        "backend": "docker",
        "cwd": str(cwd),
        "image": os.environ.get("HARNESS_SANDBOX_IMAGE") or "python:3.11-slim",
        "network": os.environ.get("HARNESS_SANDBOX_DOCKER_NETWORK") or "none",
        "probes": probes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--runtime-suite", action="store_true")
    args = parser.parse_args()
    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        dump_json({"decision": "block", "reason": "SANDBOX_CWD_NOT_FOUND", "cwd": str(cwd)})
        return 0
    dump_json(accept(cwd, args.timeout, runtime_suite=args.runtime_suite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
