#!/usr/bin/env bash
# Shared JSON output helpers for harness shell scripts.

AEL_OUTPUT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ael_pretty_enabled() {
  [[ -t 1 ]] || return 1
  case "${AEL_PRETTY:-1}" in
    0|false|False|FALSE|no|No|NO|off|Off|OFF|raw|Raw|RAW)
      return 1
      ;;
  esac
  return 0
}

ael_print_json() {
  if ael_pretty_enabled; then
    printf '%s\n' "$1" | python3 "$AEL_OUTPUT_SCRIPT_DIR/json_pretty.py"
  else
    printf '%s\n' "$1"
  fi
}
