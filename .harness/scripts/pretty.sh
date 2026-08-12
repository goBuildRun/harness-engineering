#!/usr/bin/env bash
# pretty.sh — human-readable harness JSON output.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -eq 0 ]]; then
  exec python3 "$SCRIPT_DIR/json_pretty.py"
fi

CMD="$1"
shift || true

if [[ "$CMD" != */* && -f "$SCRIPT_DIR/$CMD" ]]; then
  CMD="$SCRIPT_DIR/$CMD"
fi

case "$CMD" in
  *.sh)
    bash "$CMD" "$@" | python3 "$SCRIPT_DIR/json_pretty.py"
    ;;
  *.py)
    python3 "$CMD" "$@" | python3 "$SCRIPT_DIR/json_pretty.py"
    ;;
  *)
    "$CMD" "$@" | python3 "$SCRIPT_DIR/json_pretty.py"
    ;;
esac
