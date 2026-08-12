#!/usr/bin/env bash
echo "[DEPRECATED] 请使用: bash .harness/scripts/feedback_planner.sh" >&2
exec bash "$(dirname "$0")/../.harness/scripts/feedback_planner.sh" "$@"
