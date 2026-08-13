---
title: Git-native Harness Upgrade
status: active
updated: 2026-08-13
input_documents:
  - ARCHITECTURE.md
  - docs/design-docs/lean-enforcement.md
  - docs/Harness_成熟度评估.md
  - docs/exec-plans/tech-debt-tracker.md
---

# Git-native Harness Upgrade

本计划是 Harness Engineering 下一阶段唯一 Epic/Story backlog。Git 是唯一生命周期事实源；GitHub、CI 和托管平台不参与任务状态、commit 接受、发布资格或 provider 完成态。日常公共入口保持 `start/status/finish`。

## Requirements

| ID | Requirement |
|----|-------------|
| FR-1 | local/guarded/enforced 共用 canonical result object、attestation 和 commit verifier，不产生平行完成态。 |
| FR-2 | guarded 必须验证一次 push 的全部新增 commit，并同步对应 attestation refs。 |
| FR-3 | enforced 必须由受控 bare Git `pre-receive` 或 release gate 重验 commit；不能信任客户端自签 attestation。 |
| FR-4 | acceptance receipt 必须绑定 commit、accepted ref、task、policy、result 和 Work Item，并由受控端独立密钥签名。 |
| FR-5 | provider 终态只能消费可回查 Git 对象且签名有效的 acceptance receipt。 |
| FR-6 | 历史 workspace 原位可读，只迁移活动或重开任务。 |
| FR-7 | 所有正式任务继续按 lite/standard/strict 完整执行 GC、QA、结构、质量和 provider 要求。 |
| NFR-1 | core 只依赖 Git、Python、shell、JSON/YAML 和 OpenSSH；不要求数据库、常驻服务或 hosted CI。 |
| NFR-2 | 默认接入保持五分钟 local、低成本 guarded；enforced 基础设施按需安装。 |
| NFR-3 | verifier 和 audit 默认只读；失败必须 fail closed 且不修改 active product 或产品 workspace。 |
| NFR-4 | implementation/harness 成本继续写入同一 `result.json`，未知值保持 `unknown`。 |
| NFR-5 | generic 与产品 profile 隔离，runtime/templates 不含产品路径或托管平台生命周期默认值。 |

## Epic 1: Trusted Git Acceptance

目标：任何进入正式 ref 或正式制品的 commit 都经过受控 Git-native verifier，并可生成不可由本地调用者伪造的接受凭证。

### E1-S1 Bare receive verifier core

Status: completed (2026-08-13)

Acceptance criteria:

- 从 `pre-receive` 的 `old new ref` 输入计算正式 ref 的全部新增 commit。
- 同一 push 中 attestation ref 必须与 commit 一一对应；缺失、复制、tree/result/policy 不匹配均阻断。
- verifier 对每个 canonical result 重算 commit-bound tier、scope、policy 和机械 code-health；其他只读 gate 在后续安装故事按明确 allowlist 接入。
- verifier 不写产品 workspace，不依赖 GitHub、网络或当前 worktree。
- 删除 ref不触发错误验收；非正式 refs 不冒充 enforced。

### E1-S2 Server-signed acceptance receipts

Status: pending

Acceptance criteria:

- receive/release authority 使用独立 SSH 私钥签发 canonical receipt。
- allowed-signers 公钥轮换支持至少双钥并存。
- receipt 绑定 accepted ref 和实际接收的 attestation object。
- 篡改、跨任务、跨 commit、跨 ref 和过期 receipt 全部阻断。

### E1-S3 Enforced installation and audit

Status: pending

Acceptance criteria:

- 提供管理员内部安装/审计脚本，不新增日常公共命令。
- 临时 bare repo 端到端证明未验证 commit push 失败、验证 commit 成功。
- 只有 hook、policy、信任根和 provider terminal 链全部通过才报告 enforced。
- release gate 可复用同一 verifier 和 receipt schema。

## Epic 2: Lightweight Adoption And Migration

目标：个人与小团队不建设服务器也能低成本获得正确的 local/guarded 行为，需要强约束时再按需升级。

### E2-S1 Five-minute onboarding

Status: pending

Acceptance criteria:

- 新仓库初始化不生成托管平台 workflow 或生命周期配置。
- local/guarded 初始化、首个 lite task、finish、commit、status 可在五分钟内完成。
- public documentation 只展示 `start/status/finish` 日常路径。

### E2-S2 Guarded multi-commit reliability

Status: partially-complete

Acceptance criteria:

- initial push、fast-forward、多 ref、删除 ref和 non-fast-forward 行为有测试。
- attestation ref 同步失败不会被误报为 pass。
- hooks 明确保持 bypassable，不升级为 enforced。

### E2-S3 Workspace migration and audit

Status: pending

Acceptance criteria:

- completed legacy 原位只读，活动任务最小迁移。
- migration baseline 不伪造历史。
- audit/verifier 不修改 active product 或创建产品 workspace。

## Epic 3: Cost And Capability Closure

目标：在不牺牲既有能力的前提下量化并降低 Harness 固定成本。

### E3-S1 Real usage telemetry

Status: pending

Acceptance criteria:

- 第三方 OpenAI-compatible provider 的 implementation/harness usage receipt 完成真实联调。
- GC Agent 调用次数、上下文字符数、耗时和模型身份进入统一成本字段。
- 未提供或不可验证的成本保持 `unknown`。

### E3-S2 Capability migration closure

Status: pending

Acceptance criteria:

- BMAD、TDD、QA、GC、浏览器、安全、知识和 provider 能力均有内部 carrier 与验收测试。
- 删除被统一入口真正替代的旧脚本和重复证据要求。
- capability contract 不再使用 CI/平台生命周期术语。

### E3-S3 Rollout metrics

Status: pending

Acceptance criteria:

- lite 相比旧 L2 路径的中位上下文/证据减少至少 40%，门禁耗时减少至少 30%。
- execution tier 误降级为 0，非风险误阻断率不高于 5%。
- 指标从 result telemetry 机械汇总，不新增成本 Markdown 报告。

## Execution Order

1. E1-S1 → E1-S2 → E1-S3
2. E2-S1 → E2-S2 → E2-S3
3. E3-S1 → E3-S2 → E3-S3

Epic 1 完成前只能声明 local/guarded。每完成一个 Story，同步本文件状态、受影响权威文档和技术债；不得一次性把未验收 Story 标记完成。
