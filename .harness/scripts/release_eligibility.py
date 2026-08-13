#!/usr/bin/env python3
"""Build a release eligibility receipt from a validated commit result."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness_output import dump_json
from provider_lifecycle import build_receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--required-run-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = json.loads(Path(args.result).read_text(encoding="utf-8"))
        receipt = build_receipt(
            result, event="release", commit=args.commit,
            repository=args.repository, run_id=args.required_run_id,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        dump_json({"decision": "block", "reason": str(exc)})
        return 1
    receipt.update({"kind": "release-eligibility", "decision": "pass",
                    "required_run_id": str(args.required_run_id)})
    target = Path(args.output)
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dump_json({"decision": "pass", "reason": "RELEASE_ELIGIBILITY_READY", "receipt": receipt})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
