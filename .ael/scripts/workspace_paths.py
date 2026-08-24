#!/usr/bin/env python3
"""workspace_paths - resolve product AEL workspace paths.

This is the semantic successor of phase0_paths.py. The old module name is kept
as a compatibility shim for existing product workspaces and historical scripts.
"""
from __future__ import annotations

from phase0_paths import *  # noqa: F401,F403
from phase0_paths import main


if __name__ == "__main__":
    raise SystemExit(main())
