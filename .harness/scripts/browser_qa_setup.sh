#!/usr/bin/env bash
# browser_qa_setup.sh — verify or install Playwright Chromium for browser QA
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "$SCRIPT_DIR/browser_qa_setup.py" "$@"
