#!/usr/bin/env python3
"""Offline-only adapter fixture used to prove the Provider trace boundary."""
from __future__ import annotations

import argparse
import json
import os


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--provider", required=True)
    args = parser.parse_args()
    provider = args.provider.strip().lower()
    offline = os.environ.get("AEL_PROVIDER_OFFLINE_TRACE") == "1"
    env_bound = (
        os.environ.get("AEL_PROVIDER_EXPECTED_SUBJECT") == args.subject
        and os.environ.get("AEL_PROVIDER_NAME") == provider
    )
    if not offline or not env_bound:
        return 2
    print(json.dumps({
        "schema": "harness-provider-offline-trace-v2",
        "subject_digest": args.subject,
        "provider": provider,
        "offline": True,
        "network_calls": 0,
        "calls": ["provider.accept"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
