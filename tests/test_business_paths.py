from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from business_paths import (  # noqa: E402
    DEFAULT_BUSINESS_ROOTS,
    _platform_business_roots,
    allowlist_path,
)


class PlatformBusinessRootsTests(unittest.TestCase):
    def test_regular_product_workspace_is_not_a_platform_business_root(self) -> None:
        config = {
            "product": {"id": "sample-product", "profile": "generic"},
            "workspace": {"root": "harness-workspace"},
        }

        self.assertEqual(_platform_business_roots(config), ())

    def test_platform_product_includes_sources_and_workspace(self) -> None:
        config = {
            "platform_product": {
                "source_roots": {
                    "capability_packages": "capability-packages",
                    "scripts": "scripts",
                }
            },
            "workspace": {"root": "harness-workspace"},
        }

        self.assertEqual(
            _platform_business_roots(config),
            ("capability-packages/", "scripts/", "harness-workspace/"),
        )


class ProfileIsolationTests(unittest.TestCase):
    def test_generic_defaults_do_not_contain_product_specific_paths(self) -> None:
        self.assertIn("src/", DEFAULT_BUSINESS_ROOTS)
        self.assertNotIn("services/catalog_service/", DEFAULT_BUSINESS_ROOTS)

    def test_unknown_profile_returns_missing_profile_path(self) -> None:
        harness_root = SCRIPTS_DIR.parents[1]

        path = allowlist_path(harness_root, profile="missing-profile")

        self.assertEqual(
            path,
            harness_root / ".harness/profiles/missing-profile/package-allowlist.yaml",
        )
        self.assertFalse(path.exists())

if __name__ == "__main__":
    unittest.main()
