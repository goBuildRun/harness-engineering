#!/usr/bin/env python3
import os
import runpy
import sys

print("[DEPRECATED] 请使用: python3 .ael/scripts/browser_qa.py", file=sys.stderr)
target = os.path.join(os.path.dirname(__file__), "../.ael/scripts/browser_qa.py")
runpy.run_path(os.path.abspath(target), run_name="__main__")
