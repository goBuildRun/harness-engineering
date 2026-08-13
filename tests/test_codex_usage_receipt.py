#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".harness" / "scripts" / "codex_usage_receipt.py"
import sys
sys.path.insert(0, str(ROOT / ".harness" / "scripts"))
from codex_usage_receipt import automatic_receipt  # noqa: E402


class CodexUsageReceiptTest(unittest.TestCase):
    def _write(self, path: Path, rows: list[dict]) -> None:
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_exports_last_exact_server_total_without_conversation_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "rollout.jsonl"
            output = root / "receipt.json"
            self._write(rollout, [
                {"type": "session_meta", "payload": {"id": "session-1", "originator": "Codex Desktop", "model_provider": "openai", "timestamp": "2026-08-13T00:00:00Z"}},
                {"type": "turn_context", "payload": {"model": "gpt-test"}},
                {"type": "event_msg", "timestamp": "2026-08-13T00:01:00Z", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 3, "reasoning_output_tokens": 1, "total_tokens": 13}}}},
                {"type": "response_item", "payload": {"content": "private prompt must not appear"}},
                {"type": "event_msg", "timestamp": "2026-08-13T00:02:00Z", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 25, "cached_input_tokens": 12, "output_tokens": 8, "reasoning_output_tokens": 2, "total_tokens": 33}}}},
            ])
            result = subprocess.run([
                "python3", str(SCRIPT), "--rollout", str(rollout), "--output", str(output),
                "--task-id", "task-1", "--subject-digest", "subject-1", "--policy-digest", "policy-1",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(receipt["implementation"]["input_tokens"], 25)
            self.assertEqual(receipt["implementation"]["output_tokens"], 8)
            self.assertEqual(receipt["source"]["cached_input_tokens"], 12)
            self.assertEqual(receipt["source"]["session_id"], "session-1")
            self.assertNotIn("private prompt", output.read_text(encoding="utf-8"))

    def test_rejects_rollout_without_exact_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "rollout.jsonl"
            self._write(rollout, [{"type": "session_meta", "payload": {"id": "session-1"}}])
            result = subprocess.run([
                "python3", str(SCRIPT), "--rollout", str(rollout), "--output", str(root / "receipt.json"),
                "--task-id", "task-1", "--subject-digest", "subject-1", "--policy-digest", "policy-1",
            ], text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("CODEX_USAGE_MISSING", result.stderr)

    def test_automatic_receipt_requires_exact_thread_id_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            rollout = codex_home / "sessions" / "2026" / "08" / "13" / "rollout-session-1.jsonl"
            rollout.parent.mkdir(parents=True)
            self._write(rollout, [
                {"type": "session_meta", "payload": {"id": "session-1", "model_provider": "openai"}},
                {"type": "turn_context", "payload": {"model": "gpt-test"}},
                {"type": "event_msg", "timestamp": "2026-08-13T00:02:00Z", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 25, "output_tokens": 8, "total_tokens": 33}}}},
            ])
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home), "CODEX_THREAD_ID": "session-1"}, clear=False):
                receipt = automatic_receipt("task-1", "subject-1", "policy-1")
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt["implementation"]["input_tokens"], 25)
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home), "CODEX_THREAD_ID": "other"}, clear=False):
                self.assertIsNone(automatic_receipt("task-1", "subject-1", "policy-1"))


if __name__ == "__main__":
    unittest.main()
