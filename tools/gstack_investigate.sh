#!/usr/bin/env bash
echo "[DEPRECATED] 请使用: bash .harness/scripts/gstack_investigate.sh" >&2
exec bash "$(dirname "$0")/../.harness/scripts/gstack_investigate.sh" "$@"
