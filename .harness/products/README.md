# Product Registry Runtime State

`harness-engineering` can serve 1..N product repositories. The files in this directory are local runtime state:

- `registry.yaml` records products registered on this machine, including their default Work Item provider metadata.
- `active-product.json` records the product selected by default. It is a fallback, not the recommended context switch for parallel product work.

These two files may contain absolute local paths, so they are intentionally gitignored. Use the example files in this directory for repository documentation and bootstrapping expectations.

Create or update local state with:

```bash
bash .harness/scripts/harness_init.sh init \
  --product-root /path/to/product \
  --product-id product-id \
  --product-name "Product Name" \
  --profile generic \
  --work-item-provider feishu

bash .harness/scripts/harness_init.sh use --product-id product-id
```

When multiple products are being developed at the same time, prefer a session-scoped context:

```bash
eval "$(bash .harness/scripts/harness_product.sh env --product-id product-id)"
bash .harness/scripts/harness_product.sh exec --product-id product-id -- bash .harness/scripts/check.sh
```
