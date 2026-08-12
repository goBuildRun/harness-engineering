#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from sandbox_exec import run_remote  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
