#!/usr/bin/env bash
echo "[DEPRECATED] 请使用: bash .ael/scripts/gstack_investigate.sh" >&2
exec bash "$(dirname "$0")/../.ael/scripts/gstack_investigate.sh" "$@"
