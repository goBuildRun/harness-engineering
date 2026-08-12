#!/usr/bin/env bash
echo "[DEPRECATED] 请使用: bash .harness/scripts/agent_start.sh" >&2
exec bash "$(dirname "$0")/../.harness/scripts/agent_start.sh" "$@"
