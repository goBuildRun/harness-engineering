#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from browser_scenarios import load_scenario, run_scenario  # noqa: E402


class Locator:
    first = None

    def __init__(self, calls):
        self.calls = calls
        self.first = self

    def click(self, **kwargs): self.calls.append(("click", kwargs))
    def fill(self, value, **kwargs): self.calls.append(("fill", value, kwargs))
    def press(self, key, **kwargs): self.calls.append(("press", key, kwargs))
    def wait_for(self, **kwargs): self.calls.append(("wait", kwargs))
    def filter(self, **kwargs): self.calls.append(("filter", kwargs)); return self


class Page:
    def __init__(self): self.calls = []
    def locator(self, selector): self.calls.append(("locator", selector)); return Locator(self.calls)
    def wait_for_url(self, url, **kwargs): self.calls.append(("url", url, kwargs))


class BrowserScenariosTest(unittest.TestCase):
    def test_valid_scenario_is_bounded_and_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "login.json"
            path.write_text(json.dumps({"steps": [
                {"action": "fill", "selector": "#email", "value": "user@example.invalid"},
                {"action": "click", "selector": "button[type=submit]"},
                {"action": "expect-text", "selector": "h1", "text": "Dashboard"},
            ]}))
            steps, error = load_scenario(root, "login.json")
        self.assertEqual(error, "")
        page = Page()
        results = run_scenario(page, steps)
        self.assertEqual([item["decision"] for item in results], ["pass"] * 3)

    def test_scenario_rejects_arbitrary_or_out_of_root_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "unsafe.json"
            path.write_text(json.dumps({"steps": [{"action": "evaluate", "script": "fetch('/secret')"}]}))
            self.assertIn("STEP_INVALID", load_scenario(root, "unsafe.json")[1])
            self.assertIn("OUTSIDE_PRODUCT", load_scenario(root, "../outside.json")[1])


if __name__ == "__main__":
    unittest.main()
