from __future__ import annotations

import ast
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv"}
FORBIDDEN_IDENTIFIERS = tuple(
    bytes.fromhex(value).decode("ascii")
    for value in (
        "687a65726f",
        "6d6174726978",
        "6d61747269786167656e74",
        "7869616f7a68616e676c756f",
    )
)
FORBIDDEN_PRODUCT_PATHS = tuple(
    bytes.fromhex(value).decode("ascii")
    for value in (
        "73657276696365732f636172626f6e2f",
        "73657276696365732f6b625f736572766963652f",
        "73657276696365732f6c63615f736572766963652f",
        "73657276696365732f70726f6d6f74696f6e5f736572766963652f",
    )
)
LOCAL_PATH_RE = re.compile(r"(?:/Users/[^\s`\"']+|/private/var/[^\s`\"']+|(?<![A-Za-z0-9_])[A-Za-z]:\\[^\\\s`\"'][^\s`\"']*)")


def repository_files() -> list[Path]:
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, text=True
        )
        return [ROOT / rel for rel in output.splitlines() if rel]
    except (OSError, subprocess.CalledProcessError):
        pass
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts)
    ]


def static_string_values(path: Path) -> list[str]:
    if path.suffix != ".py":
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return []
    values: list[str] = []
    for node in ast.walk(tree):
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(value, str):
            values.append(value)
    return values


class PublicRepositoryTests(unittest.TestCase):
    def test_repository_has_no_private_product_identifiers_or_paths(self) -> None:
        issues: list[str] = []
        for path in repository_files():
            rel = path.relative_to(ROOT).as_posix()
            lowered_path = rel.lower()
            if any(term in lowered_path for term in FORBIDDEN_IDENTIFIERS):
                issues.append(f"path:{rel}")
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lowered = text.lower()
            for term in (*FORBIDDEN_IDENTIFIERS, *FORBIDDEN_PRODUCT_PATHS):
                if term in lowered:
                    issues.append(f"content:{rel}:{term}")
            for value in static_string_values(path):
                compact = re.sub(r"[^a-z0-9]", "", value.lower())
                if any(term in compact for term in FORBIDDEN_IDENTIFIERS):
                    issues.append(f"static-string:{rel}")
        self.assertEqual([], sorted(set(issues)))

    def test_tracked_content_has_no_local_state_or_absolute_paths(self) -> None:
        issues: list[str] = []
        forbidden_files = {
            ".env",
            ".env.local",
            ".product-root",
            ".ael/products/registry.yaml",
            ".ael/products/active-product.json",
        }
        for path in repository_files():
            rel = path.relative_to(ROOT).as_posix()
            if rel in forbidden_files:
                issues.append(f"local-state:{rel}")
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if rel.startswith("tests/"):
                continue
            for line in text.splitlines():
                if "LOCAL_PATH_RE" not in line and LOCAL_PATH_RE.search(line):
                    issues.append(f"absolute-path:{rel}")
        self.assertEqual([], sorted(set(issues)))


if __name__ == "__main__":
    unittest.main()
