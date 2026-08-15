#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from quality_commands import command_cwd, command_env, run_builtin, run_command  # noqa: E402


class QualityCommandsTest(unittest.TestCase):
    def test_uv_uses_product_bound_temporary_cache_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "quality_commands.os.environ", {}, clear=True
        ):
            product = Path(tmp)
            env = command_env(product, ["uv", "run", "python", "-m", "pytest"], {})
            cache = Path(env["UV_CACHE_DIR"])
            self.assertEqual(cache.name, "uv")
            self.assertEqual(cache.parent.parent.name, "harness-quality-cache")
            self.assertTrue(str(cache).startswith(tempfile.gettempdir()))
            self.assertEqual(Path(env["PYTHONPYCACHEPREFIX"]).parent, cache.parent)

    def test_uv_preserves_explicit_cache_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = command_env(
                Path(tmp), ["uv", "run", "python", "-m", "pytest"],
                {"UV_CACHE_DIR": "/configured/uv-cache"},
            )
            self.assertEqual(env["UV_CACHE_DIR"], "/configured/uv-cache")

    def test_uv_cache_ignores_inherited_user_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "quality_commands.os.environ",
            {"UV_CACHE_DIR": "/inherited/user/cache"}, clear=True,
        ):
            product = Path(tmp)
            env = command_env(product, ["uv", "run", "python", "-m", "pytest"], {})
            self.assertNotEqual(env["UV_CACHE_DIR"], "/inherited/user/cache")
            self.assertEqual(Path(env["UV_CACHE_DIR"]).name, "uv")
            self.assertEqual(
                Path(env["UV_CACHE_DIR"]).parent.parent.name,
                "harness-quality-cache",
            )

    def test_python_cache_ignores_inherited_user_cache_but_preserves_product_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "quality_commands.os.environ",
            {"PYTHONPYCACHEPREFIX": "/inherited/user/cache"}, clear=True,
        ):
            product = Path(tmp)
            isolated = command_env(product, ["python3", "-m", "compileall"], {})
            explicit = command_env(
                product, ["python3", "-m", "compileall"],
                {"PYTHONPYCACHEPREFIX": "/configured/python-cache"},
            )
            self.assertNotEqual(isolated["PYTHONPYCACHEPREFIX"], "/inherited/user/cache")
            self.assertEqual(explicit["PYTHONPYCACHEPREFIX"], "/configured/python-cache")

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
