#!/usr/bin/env bash
echo "[DEPRECATED] 请使用: bash .ael/scripts/agent_start.sh" >&2
exec bash "$(dirname "$0")/../.ael/scripts/agent_start.sh" "$@"
