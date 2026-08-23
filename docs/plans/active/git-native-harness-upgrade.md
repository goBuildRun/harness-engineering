---
title: Git-native Harness Upgrade
status: active
updated: 2026-08-13
input_documents:
  - ARCHITECTURE.md
  - docs/design/lean-enforcement.md
  - docs/operations/maturity.md
  - docs/operations/tech-debt.md
---

# Git-native Harness Upgrade

本计划是 Harness Engineering 下一阶段唯一 Epic/Story backlog。Git 是唯一生命周期事实源；GitHub、CI 和托管平台不参与任务状态、commit 接受、发布资格或 provider 完成态。日常公共流程保持 `plan/start/status/finish`，其中 `status` 只读。

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

Status: completed (2026-08-13)

Acceptance criteria:

- receive/release authority 使用独立 SSH 私钥签发 canonical receipt。
- allowed-signers 公钥轮换支持至少双钥并存。
- receipt 绑定 accepted ref 和实际接收的 attestation object。
- 篡改、跨任务、跨 commit、跨 ref 和过期 receipt 全部阻断。

Implementation note: receipt 只能由已通过 receive verifier 的 commit/ref 组合生成，Work Item 和 provider 从 canonical result 读取；默认有效期为 24 小时，信任根使用 OpenSSH `allowed_signers`，可并存多个公钥完成轮换。

### E1-S3 Enforced installation and audit

Status: completed (2026-08-13)

Acceptance criteria:

- 提供管理员内部安装/审计脚本，不新增日常公共命令。
- 临时 bare repo 端到端证明未验证 commit push 失败、验证 commit 成功。
- 只有 hook、policy、信任根和 provider terminal 链全部通过才报告 enforced。
- release gate 可复用同一 verifier 和 receipt schema。

Implementation note: 内部 `harness_enforced.py` 为受控 bare Git 安装和审计 `pre-receive` / `post-receive`。真实 Git push 验收证明未验证 commit 被拒绝、有效 commit 被接受并生成可消费 receipt；hook 偏移、信任根缺失或私钥权限暴露均使 audit fail closed。框架能力已达到 enforced，但每个产品仍须对自身实际 authority 独立审计。

### E1-S4 Authority-trusted GC receipts

Status: completed (2026-08-13)

Acceptance criteria:

- receive authority 不信任 canonical result 中客户端自报的 `mechanical+agent` 结论。
- mechanical scan 触发独立 GC 时，没有 authority 可验证 receipt 必须返回 `RECEIVE_GC_AUTHORITY_REQUIRED`。
- GC receipt 必须绑定 commit、task、policy、触发信号、上下文摘要和 Agent telemetry，并纳入与 acceptance receipt 相同的 SSH trust root。
- strict 缺 receipt、receipt 被篡改、跨 commit/policy 复用或 Agent 调用超过一次均阻断。
- 完成前，enforced 只能声明覆盖无 GC Agent 信号的 commit；不得声称所有 execution tier 已闭合。

Implementation note: `harness_gc_receipt.py` 使用独立 `harness-gc-review` SSH namespace 签署最小 receipt，并通过 `refs/harness/gc/<commit>` 传输。receive authority 从目标 commit 重建受限 context，重算 context digest 和 triggers，校验 task/policy/telemetry，且只允许一次 Agent 调用。GC reviewer 可使用独立私钥，其公钥与 acceptance authority 一并由同一个 `allowed_signers` trust root 管理。

## Epic 2: Lightweight Adoption And Migration

目标：个人与小团队不建设服务器也能低成本获得正确的 local/guarded 行为，需要强约束时再按需升级。

### E2-S1 Five-minute onboarding

Status: completed (2026-08-13)

Acceptance criteria:

- 新仓库初始化不生成托管平台 workflow 或生命周期配置。
- local/guarded 初始化、首个 lite task、finish、commit、status 可在五分钟内完成。
- public documentation 只展示 `plan/start/status/finish` 日常路径，其中 `status` 只读。

Implementation note: 临时新 Git 仓库通过 `start → finish → status` 的 lite 路径在五分钟预算内完成且不生成托管平台目录；这是显式规划之外的低风险例外。Harness 自动生成的 `runs/` 与 lite `task.json` 仍纳入完整 subject/attestation，但不参与 execution tier、scope 或 code-health 风险分类。

### E2-S2 Guarded multi-commit reliability

Status: completed (2026-08-13)

Acceptance criteria:

- initial push、fast-forward、多 ref、删除 ref和 non-fast-forward 行为有测试。
- attestation ref 同步失败不会被误报为 pass。
- hooks 明确保持 bypassable，不升级为 enforced。

### E2-S3 Workspace migration and audit

Status: completed (2026-08-13)

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

Implementation status: schema、task/subject/policy-bound usage receipt、implementation/harness 分项、GC provider/model/calls/context/duration telemetry 均已完成。当前环境未提供可发现的第三方 OpenAI-compatible endpoint/model/key，真实 usage response 联调仍为外部验收阻塞；不得用 mock 或 0 代替。

### E3-S2 Capability migration closure

Status: completed (2026-08-13)

Acceptance criteria:

- BMAD、TDD、QA、GC、浏览器、安全、知识和 provider 能力均有内部 carrier 与验收测试。
- 删除被统一入口真正替代的旧脚本和重复证据要求。
- capability contract 不再使用 CI/平台生命周期术语。

Implementation note: capability contract 的 GC carrier 已从旧 HTTPS/CI adapter 切换为 Git-native signed GC receipt，commit acceptance carrier 覆盖 attestation、receive 和 enforced authority；旧 adapter 仅保留为可选 runner，不参与生命周期权威。

### E3-S3 Rollout metrics

Status: implementation-complete; rollout-data-pending

Acceptance criteria:

- lite 相比旧 L2 路径的中位上下文/证据减少至少 40%，门禁耗时减少至少 30%。
- execution tier 误降级为 0，非风险误阻断率不高于 5%。
- 指标从 result telemetry 机械汇总，不新增成本 Markdown 报告。

Implementation note: `harness_metrics.py` 只读汇总 canonical result，输出 lite 中位 context/gate duration/checks、tier downgrade 和 block rate。少于 5 个 lite 样本或 baseline 为 `unknown` 时返回 `ROLLOUT_METRICS_INSUFFICIENT_DATA`，不会伪造阈值通过。

## Epic 4: Story Cycle Efficiency

目标：在不降低 L0/L1、独立 QA、生产真实性和 Git-native acceptance 约束的前提下，把 strict/L3 同等级 Story 从用户确认到 `ready_to_release` 的最大墙钟预算压缩到 30 分钟以内；30 分钟是止损上限，实际 P50/P95 应继续低于并逐步压缩该上限，同时对超时、等待、重试和未知量做可审计归因。

### E4-S1 30-minute strict story cycle

Status: implementation-complete; independent-qa-pass; live-strict-trial-pending (2026-08-22)

Local execution package: [story-cycle-efficiency-30m.md](./story-cycle-efficiency-30m.md)

Ownership:

- `owner: harness-engineering`
- `provider: noop`
- external tasklist / Epic / Work Item: `unknown`
- external sync: `prohibited_pending_verified_binding`
- 小张罗 Story 43.5、Epic 43 和产品任务清单仅为审计来源，不是本 Epic 的父级或同步目标。

Acceptance criteria:

- 以无消息正文、密钥或生产参数的结构化事件重建 Story wall-clock，并区分墙钟、可证明工具等待和 `unknown` Agent active。
- 默认最大预算不超过 30 分钟；每阶段有预算、重试上限、超时归因和停止无界循环的结构化结果，实际运行目标应低于上限。
- `finish` 验证期间不自动写 planning、Growth 或其他 subject 输入；聚合动作退出关键路径。
- 确定性 gate 以真实依赖 fingerprint 精确失效；QA、GC Agent、浏览器、部署、Provider、回滚不得跨 subject 缓存。
- 无共享写状态的 gate 可并行；有真实依赖、写副作用或未声明并行安全的产品质量命令保持串行。
- QA 共享机械证据可去重，但每个任务继续保留独立 reviewer 签章和 subject/policy/report digest 绑定。
- 真实 Provider 只能在版本化离线 verifier preflight 通过后调用一次；本 Story 只用 fixture/mock 验证，不触发生产调用。
- lifecycle capability 在 Story 开始时生成本地 receipt；未知能力保持 pending/block，不在收口阶段猜测映射。
- assurance 仍为 `guarded`、`bypassable: true`；Judge、P95、L3 和未提供遥测保持 `unknown`/`pending`。

## Execution Order

1. E1-S1 → E1-S2 → E1-S3 → E1-S4
2. E2-S1 → E2-S2 → E2-S3
3. E3-S1 → E3-S2 → E3-S3
4. E4-S1 独立于外部产品 backlog；先完成离线 fixture、独立 QA 和 guarded commit，再进行真实 Story 试运行。

Epic 1 完成前只能对通过实际 authority audit 且未触发未闭合信任链的 commit 声明 enforced。每完成一个 Story，同步本文件状态、受影响权威文档和技术债；不得一次性把未验收 Story 标记完成。
