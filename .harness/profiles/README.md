# Harness Profiles

Profiles isolate product-domain rules from the reusable harness-engineering runtime.

Use a profile when a product needs its own package allowlist, domain references, or prompt constraints:

```text
.harness/profiles/<profile>/
└── package-allowlist.yaml
```

Built-in profiles:

| Profile | Use case |
|---------|----------|
| `generic` | Brownfield onboarding baseline. Permissive for normal project paths, still blocks obvious secret/cache paths. |

Resolution order:

1. `.harness/profiles/<active-profile>/package-allowlist.yaml`
2. `.harness/rules/package-allowlist.yaml` generic compatibility fallback when the selected profile is `generic`

An explicitly selected unknown profile blocks; it never falls back to generic or another product policy. Product-specific profiles must be supplied explicitly by the adopting organization.

The active profile comes from the product workspace config at `<product-root>/harness-workspace/project.yaml`.
