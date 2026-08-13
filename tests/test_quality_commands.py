#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from quality_commands import command_cwd, run_builtin, run_command  # noqa: E402


class QualityCommandsTest(unittest.TestCase):
    def test_command_cwd_runs_inside_nested_product_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            nested = product / "backend"
            nested.mkdir()
            (nested / "sample.py").write_text("value = 1\n")
            result = run_command(
                product, "nested syntax", ["python3", "-m", "compileall", "-q", "sample.py"],
                {}, 30, "backend",
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["cwd"], "backend")

    def test_command_cwd_rejects_absolute_parent_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            product = Path(tmp)
            (product / "escape").symlink_to(Path(outside), target_is_directory=True)
            for value in ("../outside", str(Path(outside)), "escape"):
                resolved, reason = command_cwd(product, value)
                self.assertIsNone(resolved)
                self.assertIn("QUALITY_COMMAND_UNSAFE_CWD", reason or "")

    def test_python_import_boundary_blocks_forbidden_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            source = product / "services/api/handler.py"
            source.parent.mkdir(parents=True)
            source.write_text("from services.db.models import User\n")
            result = run_builtin(product, {
                "builtin": "python-import-boundaries", "paths": ["services"],
                "boundaries": [{"from": "services/api", "forbid": ["services.db"]}],
            })
            self.assertFalse(result["ok"])
            self.assertIn("forbidden import services.db.models", result["issues"][0])

    def test_python_import_boundary_allows_unlisted_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            source = product / "services/api/handler.py"
            source.parent.mkdir(parents=True)
            source.write_text("from services.core.users import load_user\n")
            result = run_builtin(product, {
                "builtin": "python-import-boundaries", "paths": ["services"],
                "boundaries": [{"from": "services/api", "forbid": ["services.db"]}],
            })
            self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
