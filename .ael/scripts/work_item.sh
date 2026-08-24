#!/usr/bin/env bash
# work_item.sh — 可替换协同系统统一入口（noop / Teambition / 飞书 / Jira）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
EMIT="$SCRIPT_DIR/emit_json.py"

CMD="${1:-}"
shift || true

case "$CMD" in
  draft-spec)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" draft-spec "$@"
    ;;
  sync-spec)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" sync-spec "$@"
    ;;
  verify)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" verify "$@"
    ;;
  pull)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" pull "$@"
    ;;
  close)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" close "$@"
    ;;
  update-description)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" update-description "$@"
    ;;
  update-title)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" update-title "$@"
    ;;
  create-subtask)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" create-subtask "$@"
    ;;
  gate)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" gate "$@"
    ;;
  extract)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" extract "$@"
    ;;
  diagnose)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" diagnose "$@"
    ;;
  capabilities)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" capabilities "$@"
    ;;
  scenario-configs)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" scenario-configs "$@"
    ;;
  list-mine)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" list-mine "$@"
    ;;
  feishu-tasklist-member)
    exec python3 "$SCRIPT_DIR/work_item.py" --ael-root "$AEL_ROOT" feishu-tasklist-member "$@"
    ;;
  "")
    python3 "$EMIT" block "USAGE: work_item.sh <draft-spec|sync-spec|verify|pull|close|update-description|update-title|create-subtask|gate|extract|diagnose|capabilities|scenario-configs|list-mine|feishu-tasklist-member> ..."
    ;;
  *)
    python3 "$EMIT" block "UNKNOWN_CMD: $CMD"
    ;;
esac
