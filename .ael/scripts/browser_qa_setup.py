#!/usr/bin/env python3
"""Install or verify Playwright Chromium for browser QA."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

from ael_output import dump_json


def emit(decision: str, reason: str, **extra: Any) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout or "")[-4000:]


def check_playwright() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "PLAYWRIGHT_PYTHON_PACKAGE_MISSING"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
    except Exception as exc:
        return False, f"PLAYWRIGHT_CHROMIUM_UNAVAILABLE: {exc}"
    return True, "PLAYWRIGHT_READY"


def install_playwright(with_package: bool) -> tuple[bool, str]:
    if with_package:
        code, out = run([sys.executable, "-m", "pip", "install", "playwright"])
        if code != 0:
            return False, f"PLAYWRIGHT_PACKAGE_INSTALL_FAILED: {out}"
    code, out = run([sys.executable, "-m", "playwright", "install", "chromium"])
    if code != 0:
        return False, f"PLAYWRIGHT_CHROMIUM_INSTALL_FAILED: {out}"
    return check_playwright()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=("check", "install"))
    parser.add_argument("--install-package", action="store_true", help="先 pip install playwright")
    args = parser.parse_args()

    if args.cmd == "check":
        ok, reason = check_playwright()
    else:
        ok, reason = install_playwright(args.install_package)
    emit("pass" if ok else "block", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
