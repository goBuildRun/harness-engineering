#!/usr/bin/env bash
echo "[DEPRECATED] 请使用: bash .ael/scripts/feedback_planner.sh" >&2
exec bash "$(dirname "$0")/../.ael/scripts/feedback_planner.sh" "$@"
