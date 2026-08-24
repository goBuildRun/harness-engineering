#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import release_preflight  # noqa: E402


class ReleasePreflightTest(unittest.TestCase):
    def test_flags_active_product_copies_as_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            products = root / ".ael" / "products"
            products.mkdir(parents=True)
            (products / "active-product 2.json").write_text('{"product_id":"demo"}\n', encoding="utf-8")
            (products / "active-product.example.json").write_text('{"product_id":"example"}\n', encoding="utf-8")

            stdout = io.StringIO()
            with patch("sys.argv", ["release_preflight.py", "--ael-root", str(root)]), patch("sys.stdout", stdout):
                release_preflight.main()

            data = json.loads(stdout.getvalue())

        self.assertEqual("block", data["decision"])
        self.assertIn("LOCAL_STATE_FILE:.ael/products/active-product 2.json", data["issues"])
        self.assertNotIn("LOCAL_STATE_FILE:.ael/products/active-product.example.json", data["issues"])


if __name__ == "__main__":
    unittest.main()
