#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ci_gc_review import review  # noqa: E402


class Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class CiGcReviewTest(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "product"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
        (repo / "module.py").write_text("def live():\n    return 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        (repo / "module.py").write_text("def unused_helper():\n    pass\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "change"], cwd=repo, check=True)
        return repo

    def test_required_review_sends_parent_to_target_commit_patch_and_binds_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = self._repo(Path(tmp))
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=product, text=True).strip()
            captured = {}

            def respond(request, **_kwargs):
                captured["payload"] = json.loads(request.data)
                payload = captured["payload"]
                return Response({
                    "decision": "pass", "role": "gc-sweeper", "independent": True,
                    "task_id": payload["task_id"], "subject_digest": sha,
                    "policy_digest": payload["policy_digest"], "findings": 0,
                    "remediated": 0, "deferred_work_items": [],
                })

            args = type("Args", (), {
                "product_root": str(product), "harness_root": str(ROOT),
                "task_id": "task-1", "commit": sha, "tier": "standard", "scope": ["."],
            })()
            with patch.dict(os.environ, {
                "HARNESS_GC_REVIEW_URL": "https://gc.example.invalid/review",
                "HARNESS_GC_REVIEW_TOKEN": "token",
            }, clear=False), patch("urllib.request.urlopen", side_effect=respond):
                result = review(args)
            self.assertEqual(result["decision"], "pass")
            self.assertGreater(result["telemetry"]["context_chars"], 0)
            self.assertIn("unused_helper", captured["payload"]["context"]["actual_diff"])
            self.assertEqual(captured["payload"]["context"]["task_contract"]["scope"], ["."])

    def test_required_review_without_endpoint_blocks_only_when_gc_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = self._repo(Path(tmp))
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=product, text=True).strip()
            args = type("Args", (), {
                "product_root": str(product), "harness_root": str(ROOT),
                "task_id": "task-1", "commit": sha, "tier": "standard", "scope": ["."],
            })()
            with patch.dict(os.environ, {}, clear=True):
                result = review(args)
            self.assertEqual(result["decision"], "block")
            self.assertEqual(result["reason"], "GC_REVIEW_CONFIG_MISSING")
            (product / "notes.md").write_text("docs only\n")
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "docs"], cwd=product, check=True)
            args.commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=product, text=True
            ).strip()
            args.tier = "lite"
            with patch.dict(os.environ, {}, clear=True):
                lite = review(args)
            self.assertEqual(lite["decision"], "pass")
            self.assertFalse(lite["required"])

    def test_review_rejects_invalid_task_id_before_readback_or_network(self) -> None:
        args = type("Args", (), {
            "product_root": "/not/read", "harness_root": str(ROOT),
            "task_id": "../../escape", "commit": "HEAD", "tier": "standard", "scope": ["."],
        })()
        with patch("ci_gc_review.resolve_commit") as resolve, patch("urllib.request.urlopen") as request:
            result = review(args)
        self.assertEqual(result, {"decision": "block", "reason": "TASK_ID_INVALID"})
        resolve.assert_not_called()
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
