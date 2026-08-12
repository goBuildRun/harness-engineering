#!/usr/bin/env python3
"""Pretty-print harness JSON output for humans.

Harness hooks keep stdout as compact JSON for machine parsing. Pipe their output
through this helper when reading results in a terminal.
"""
from __future__ import annotations

import json
import sys
from typing import Any


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> int:
    text = sys.stdin.read()
    if not text.strip():
        return 0

    try:
        print_json(json.loads(text))
        return 0
    except json.JSONDecodeError:
        pass

    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            print_json(json.loads(line))
        except json.JSONDecodeError:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
