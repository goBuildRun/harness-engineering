#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
ROOT = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))

from sandbox_exec import docker_command, main as sandbox_main, run_remote  # noqa: E402
from sandbox_acceptance import accept  # noqa: E402


class Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class SandboxExecTest(unittest.TestCase):
    def test_docker_command_mounts_cwd_disables_network_and_preserves_argv(self) -> None:
        cwd = Path("/tmp/product root")
        argv = ["python3", "-c", "print('ok')", "value with spaces", "$literal"]
        with patch.dict(os.environ, {}, clear=True):
            command = docker_command(argv, cwd)
        self.assertEqual(command[0:5], ["docker", "run", "--rm", "--network", "none"])
        self.assertIn(f"{cwd}:/workspace", command)
        self.assertEqual(command[-len(argv):], argv)

    def test_live_acceptance_fails_if_any_probe_breaks(self) -> None:
        with patch("sandbox_acceptance.run_probe", side_effect=[(True, ""), (False, "connected"), (True, "")]):
            result = accept(Path("/tmp/product"), 10)
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["probes"][1]["name"], "network-isolated")
        self.assertEqual(result["probes"][1]["decision"], "block")

    def remote_env(self) -> dict[str, str]:
        return {
            "HARNESS_SANDBOX_REMOTE_URL": "https://executor.example.invalid/v1/run",
            "HARNESS_SANDBOX_WORKSPACE_REF": "repo@commit",
            "HARNESS_SANDBOX_REMOTE_TOKEN": "secret",
            "HARNESS_SANDBOX_SUBJECT_DIGEST": "abc123",
        }

    def test_remote_backend_sends_structured_argv_and_binds_subject(self) -> None:
        response = Response({
            "schema_version": 1, "decision": "pass",
            "reason": "REMOTE_EXEC_SUCCESS", "subject_digest": "abc123",
        })
        with patch.dict(os.environ, self.remote_env(), clear=False), patch(
            "urllib.request.urlopen", return_value=response,
        ) as opened:
            ok, reason = run_remote(["python3", "-m", "unittest"], 60)
        self.assertTrue(ok)
        self.assertEqual(reason, "REMOTE_EXEC_SUCCESS")
        request = opened.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["argv"], ["python3", "-m", "unittest"])
        self.assertEqual(payload["network"], "none")
        self.assertNotIn("secret", request.data.decode())

    def test_remote_backend_fails_closed_on_config_or_subject_mismatch(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("CONFIG_INVALID", run_remote(["true"], 10)[1])
        response = Response({
            "schema_version": 1, "decision": "pass",
            "reason": "REMOTE_EXEC_SUCCESS", "subject_digest": "stale",
        })
        with patch.dict(os.environ, self.remote_env(), clear=False), patch(
            "urllib.request.urlopen", return_value=response,
        ):
            ok, reason = run_remote(["true"], 10)
        self.assertFalse(ok)
        self.assertEqual(reason, "REMOTE_SANDBOX_SUBJECT_MISMATCH")

    def test_main_exit_code_matches_machine_readable_decision(self) -> None:
        emitted = []
        with patch("sandbox_exec.emit", side_effect=lambda decision, reason, **extra: emitted.append({
            "decision": decision, "reason": reason, **extra,
        })), patch.object(sys, "argv", ["sandbox_exec.py"]):
            self.assertEqual(sandbox_main(), 1)
        self.assertEqual(emitted[-1]["decision"], "block")

        with patch("sandbox_exec.emit", side_effect=lambda decision, reason, **extra: emitted.append({
            "decision": decision, "reason": reason, **extra,
        })), patch("sandbox_exec.run_controlled", return_value=(False, "TEST_BLOCK")), \
                patch.object(sys, "argv", [
                    "sandbox_exec.py", "--cwd", str(ROOT), "--", "python3", "-V",
                ]):
            self.assertEqual(sandbox_main(), 1)
        self.assertEqual(emitted[-1]["decision"], "block")

        with patch("sandbox_exec.emit", side_effect=lambda decision, reason, **extra: emitted.append({
            "decision": decision, "reason": reason, **extra,
        })), patch("sandbox_exec.run_controlled", return_value=(True, "TEST_PASS")), \
                patch.object(sys, "argv", [
                    "sandbox_exec.py", "--cwd", str(ROOT), "--", "python3", "-V",
                ]):
            self.assertEqual(sandbox_main(), 0)
        self.assertEqual(emitted[-1]["decision"], "pass")

    def test_shell_wrapper_without_command_returns_nonzero_block(self) -> None:
        completed = subprocess.run(
            ["bash", str(ROOT / ".harness/scripts/run_in_sandbox.sh")],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(completed.stdout)["decision"], "block")


if __name__ == "__main__":
    unittest.main()
