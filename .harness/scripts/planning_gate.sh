#!/usr/bin/env bash
# planning_gate.sh — BMAD Planning → Harness Execution 准入门禁（语义入口）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/bmad_entry_gate.sh" "$@"
