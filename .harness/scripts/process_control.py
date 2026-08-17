#!/usr/bin/env python3
"""Bounded local subprocess execution with descendant cleanup."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


TERMINATE_GRACE_SECONDS = 1


def _terminate_remaining_group(process_group_id: int) -> None:
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _terminate_process_group(process: subprocess.Popen[str]) -> str:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        output, _ = process.communicate(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        output, _ = process.communicate()
    else:
        _terminate_remaining_group(process.pid)
    return output or ""


def run_process_group(command: list[str], *, cwd: Path | None = None,
                      env: dict[str, str] | None = None,
                      timeout: int) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command, cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        final_output = _terminate_process_group(process)
        if final_output:
            exc.output = final_output
        raise
    _terminate_remaining_group(process.pid)
    return subprocess.CompletedProcess(command, process.returncode, output or "", None)
