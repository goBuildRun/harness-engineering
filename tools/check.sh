#!/usr/bin/env bash
echo "[DEPRECATED] 请使用: bash .harness/scripts/check.sh" >&2
exec bash "$(dirname "$0")/../.harness/scripts/check.sh" "$@"
