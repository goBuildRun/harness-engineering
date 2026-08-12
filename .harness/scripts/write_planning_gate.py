#!/usr/bin/env python3
"""Semantic entry for writing Planning Gate credentials.

The implementation is shared with write_phase0.py so older products keep their
compatibility files while new scripts can call the Planning Gate name.
"""
from __future__ import annotations

from write_phase0 import main


if __name__ == "__main__":
    raise SystemExit(main())
