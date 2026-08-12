#!/usr/bin/env bash
# memory-sweep.sh — 熵减扫描（GC 触发前机械污点检测）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
EMIT="$SCRIPT_DIR/emit_json.py"

TASK_ID="${1:-}"
# 默认只扫业务目录；不存在时扫仓库根但排除 harness 基础设施
SCAN_DIRS=()
for d in src app apps packages services; do
  [[ -d "$ROOT_DIR/$d" ]] && SCAN_DIRS+=("$ROOT_DIR/$d")
done

if [[ ${#SCAN_DIRS[@]} -eq 0 ]]; then
  SCAN_DIRS=("$ROOT_DIR")
  EXCLUDE_PATHS='\.harness/|/tools/|/docs/|node_modules|\.git'
else
  EXCLUDE_PATHS='node_modules|\.git'
fi

if [[ -z "$TASK_ID" ]]; then
  python3 "$EMIT" block "ERROR: 必须传入任务编号 (如 T1)"
  exit 0
fi

FINDINGS=()

# AI 过程注释
for SCAN_ROOT in "${SCAN_DIRS[@]}"; do
while IFS= read -r hit; do
  FINDINGS+=("AI_COMMENT: $hit")
done < <(grep -rEn 'TODO:\s*AI|FIXME:\s*agent|AI fixed' "$SCAN_ROOT" \
  --include='*.ts' --include='*.tsx' --include='*.js' --include='*.py' --include='*.go' --include='*.java' \
  2>/dev/null | grep -Ev "$EXCLUDE_PATHS" | head -20 || true)

# 调试输出（排除 test/spec 与 harness CLI）
while IFS= read -r hit; do
  FINDINGS+=("DEBUG_PRINT: $hit")
done < <(grep -rEn 'console\.log\(|println!\(|^\s*print\(' "$SCAN_ROOT" \
  --include='*.ts' --include='*.tsx' --include='*.js' --include='*.py' \
  2>/dev/null | grep -Ev '/(test|spec|__tests__)/' | grep -Ev "$EXCLUDE_PATHS" | head -20 || true)
done

if [[ ${#FINDINGS[@]} -gt 0 ]]; then
  REASON=$(printf '%s; ' "${FINDINGS[@]}")
  python3 "$EMIT" block "GC_DIRTY: 任务 ${TASK_ID} 扫描到污点。请 gc-sweeper 清理后重试。${REASON}"
  exit 0
fi

python3 "$EMIT" pass "GC_CLEAN: 任务 ${TASK_ID} 未检测到常见 AI 残渣；gc-sweeper 可做最终人工式扫尾"
