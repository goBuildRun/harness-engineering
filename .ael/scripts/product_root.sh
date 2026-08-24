#!/usr/bin/env bash
# product_root.sh — resolve the product repository root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PRODUCT_ROOT_ARG=""
PRODUCT_ID_ARG=""

usage() {
  cat <<'EOF'
USAGE: product_root.sh [--product-root PATH] [--product-id ID]

Resolver precedence:
  1. --product-root / --product-id
  2. AEL_PRODUCT_ROOT / AEL_PRODUCT_ID
  3. cwd product workspace discovery
  4. .ael/products/active-product.json
  5. legacy marker/search fallback
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --product-root)
      PRODUCT_ROOT_ARG="${2:-}"
      shift 2
      ;;
    --product-id)
      PRODUCT_ID_ARG="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "UNKNOWN_ARG: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

exec python3 "$SCRIPT_DIR/product_context.py" resolve-root \
  --ael-root "$AEL_ROOT" \
  --product-root "$PRODUCT_ROOT_ARG" \
  --product-id "$PRODUCT_ID_ARG"
