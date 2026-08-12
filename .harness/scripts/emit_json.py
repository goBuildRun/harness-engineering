#!/usr/bin/env python3
"""Emit harness feedback JSON to stdout (always exit 0)."""
import sys

from harness_output import emit

def main():
    if len(sys.argv) < 3:
        emit("block", "USAGE: emit_json.py <pass|block> <reason>")
        return
    decision = sys.argv[1]
    reason = " ".join(sys.argv[2:])
    if decision not in ("pass", "block"):
        decision = "block"
        reason = f"Invalid decision; {reason}"
    emit(decision, reason)

if __name__ == "__main__":
    main()
