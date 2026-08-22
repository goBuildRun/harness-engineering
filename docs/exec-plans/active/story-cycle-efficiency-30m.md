---
title: Story Cycle Efficiency Under 30 Minutes
status: implementation-complete
updated: 2026-08-22
owner: harness-engineering
baseline_commit: 2ea2c8e3148e767718aa66c7a8f0ae850a5201cc
independent_qa: pass
live_strict_trial: pending
local_epic_id: E4
local_story_id: E4-S1
work_item_provider: noop
external_tasklist_id: unknown
external_epic_id: unknown
external_work_item_id: unknown
external_sync: prohibited_pending_verified_binding
execution_tier: strict
assurance: guarded
bypassable: true
audit_source_story_id: e68364af-2434-4cbf-ac2d-202faa1b1879
audit_source_parent_epic_id: d5cd323b-ac09-45dc-9084-dd07fd8a2d40
audit_source_tasklist_id: 61fbcf0a-a08d-4200-8110-b4a46b7dc9a4
audit_source_relationship: audit_source_only_not_parent
---

# Story Cycle Efficiency Under 30 Minutes

Audit and benchmark: [Story 43.5 efficiency audit](./story-43-5-efficiency-audit.md)

## Goal

把 strict/L3 同等级 Story 从用户确认到 `ready_to_release` 的默认墙钟预算压到 30 分钟以内，同时保留 L0/L1、独立 QA、真实 Provider、Git-native acceptance、隐私和隔离约束。超时必须停止自动循环并输出可审计归因；未知量不得补零或猜测。

## Readiness

- Harness 外部 tasklist/Epic 身份无法从版本化配置证明，因此本迭代只进入 Harness 唯一本地 backlog，`provider: noop`，禁止外部同步。
- 小张罗 Story 43.5 的产品提交、DeerFlow 提交、生产证据和飞书状态只读；不重新部署、不调用 Provider/Judge、不关闭任务。
- 审计源 Story 的 Harness input/output Token 保持 `unknown`，Harness telemetry 保持 `partial`，L3 保持 `live_validation_pending`；不得用 Codex 或 Provider 数据补齐。
- `products/zhangluo/harness-workspace/knowledge/LESSONS.md` 与 12 个历史未跟踪文件是受保护基线。
- 当前工作树中的 provider lifecycle 安全改动属于另一项工作，不纳入本 Story 的提交。

## Acceptance Criteria

1. Given 一个 Story 在用户确认时开始，when 各阶段写入无正文的开始/结束事件，then root wall-clock、阶段 wall、工具等待、重试、超时和 unknown active 可机械重建，平行 span 不重复相加。
2. Given 默认 strict 预算，when 任一阶段或总预算耗尽，then Harness 在新 gate/Provider 副作用前停止，并返回阶段、预算、已用时间、attempt 和 remediation；同一输入的 `finish` 最多自动尝试两次。
3. Given knowledge/Growth 陈旧，when `finish` 验证，then 产品树保持 byte-for-byte 不变并返回显式 remediation，不在 gate 内 autosync 后制造 `INPUT_CHANGED`。
4. Given 多个无共享写状态 gate，when 执行 gate plan，then 它们并行且结果顺序稳定；产品 lint/test、浏览器、部署、Provider 和有依赖 gate 保持串行。
5. Given只变化 Growth、说明或无关证据，when 再次 `finish`，then gate-specific input digest 允许确定性检查复用并重新绑定当前 subject；源码、工具、policy 或真实依赖变化时对应检查 miss。
6. Given QA 覆盖多个任务，when共享一次机械检查，then每个任务仍有独立 `qa-evaluator` receipt，并绑定 current subject、policy、paths、TEST/REVIEW digest；实现者自签或旧 subject 必须 block。
7. Given Product Spec 要求 real Provider，when准备生产验收，then版本化 offline verifier contract/fixture 先 pass 且 receipt 绑定 subject；`provider_attempt.py` 在 deploy 阶段预算内原子占用唯一执行机会，失败也消耗额度，strict evidence 回验同一 digest 链。本 Story 的验证不得触网。
8. Given Story 启动，when读取本地 provider 配置，then在实现前生成 lifecycle capability receipt，明确 `ready_to_release` 映射、readback 和 unknown/pending；不在最终回读时首次发现缺口。
9. Given任何 CLI receipt，when decision 是 block，then机器可检查 decision/exit/readback 一致性；兼容迁移不得让现有 hook 静默误判，未完成的全局切换必须显式列为风险。
10. Given优化后的离线 fixture，when运行基准和独立 QA，then报告 before/after、质量不变式证明、unknown 和后续真实 Story 实测方案；不宣称 mock 等于生产 P95。

## Default Budget

| Stage | Budget | Dependency |
|-------|--------|------------|
| takeover / scope / lifecycle capability | 2m | user confirmation |
| delta planning + Planning Gate | 3m | takeover |
| implementation + targeted tests | 10m | planning |
| independent QA + bounded repair | 7m | candidate diff; independent actor |
| deploy preflight + deploy + one Provider acceptance | 5m | local gates + QA |
| GC / finish / Token / commit / lifecycle readback | 3m | production evidence |

Total: `30m`. Agent active time remains `unknown` until the host supplies explicit spans; wall-clock and tool wait do not impersonate it.

## Implementation DAG

| ID | Read boundary | Write boundary | Action | Verification | Done condition |
|----|---------------|----------------|--------|--------------|----------------|
| T1 | gate/runtime/telemetry | timing runtime + result integration | wall-clock ledger, budgets, retry cap | focused unit tests | deterministic ledger and stop reasons |
| T2 | gate/cache/state | gate planner + cache | pure validation, safe parallelism, dependency digests | concurrency/cache fixtures | stable order, precise hit/miss, no writes |
| T3 | QA evidence scripts | QA bundle/check | shared mechanical bundle + independent receipts | stale/self-sign fixtures | digest-bound independent sign-off |
| T4 | production/lifecycle contracts | offline preflight + start receipt | preflight before side effect and early capability discovery | offline provider fixtures | no network, fail closed |
| T5 | Story 43.5 metadata-only evidence | audit + benchmark report | timeline, Pareto, flow/value classification, before/after | arithmetic/fixture tests | 9:34:36.120 closes; unknown explicit |
| T-GC | all E4-S1 changes | no business behavior | independent review and regression scan | full Harness test suite | no L0/L1 weakening |

## Non-goals And Deferred Risks

- 不把小张罗 route、call 名或飞书容器 ID硬编码为 Harness 通用规则。
- 不重跑生产 Provider/Judge，不把离线 benchmark 冒充生产 P95。
- 全局 JSON decision/exit-code 迁移若无法一次更新所有 shell/hook 调用者，只交付版本化 contract checker 与迁移清单，不做破坏性半切换。
- 阶段状态机可在每次受控 transition、gate 和 Provider side effect 前停止，但不能中断未通过 Harness 入口运行的 host 推理；assurance 因此保持 `guarded` / `bypassable: true`。
- Codex host 长会话的 2.52 亿 input Token 不能仅靠当前 5.4k 字符的 Harness context excerpt 解释；本轮提供 digest/index 与阶段遥测，host 侧阶段新会话/摘要协议留待实测。Harness input/output Token 仍为 `unknown`，telemetry 仍为 `partial`，L3 仍为 `live_validation_pending`。

## Verification

- Final unit suite on the mixed local tree: `400 tests` passed in `72.668s`; this includes concurrent, intentionally unstaged work.
- Exact staged E4 commit snapshot: `396 tests` passed in `71.299s`.
- Independent QA: `pass`; no blocker remains on the frozen implementation.
- Manifest and smoke validation on the exact staged snapshot: `HARNESS_VALID`.
- Offline benchmark: `400ms -> 120ms` first run and unrelated-evidence rerun (`70%` reduction).
- Production P95, Judge usage and the next live strict/L3 Story result remain `unknown`/`pending`.

## Suggested Review Order

**Lifecycle and wall clock**

- Start with enforced stage order, bounded retries, and canonical-only root closure.
  [`harness_cycle_commands.py:36`](../../../.harness/scripts/harness_cycle_commands.py#L36)

- Confirm finish computes gates before closing a successful Story exactly once.
  [`harness_commands.py:186`](../../../.harness/scripts/harness_commands.py#L186)

- Verify budgets stop serial and parallel work before another side effect.
  [`harness_gate_execution.py:282`](../../../.harness/scripts/harness_gate_execution.py#L282)

**Provider truth and lifecycle capability**

- Inspect one-shot claims and fresh evidence SHA-256 binding.
  [`provider_attempt.py:51`](../../../.harness/scripts/provider_attempt.py#L51)

- Check strict evidence resolves and hashes the actual product-tree file.
  [`harness_strict_evidence.py:14`](../../../.harness/scripts/harness_strict_evidence.py#L14)

- Review offline lifecycle discovery before strict implementation begins.
  [`harness_lifecycle_preflight.py:32`](../../../.harness/scripts/harness_lifecycle_preflight.py#L32)

**Caching, QA, and context**

- Trace dependency-specific digests that drive precise cache invalidation.
  [`harness_gate_execution.py:154`](../../../.harness/scripts/harness_gate_execution.py#L154)

- Confirm shared mechanical QA still requires independent per-task receipts.
  [`qa_evidence_binding.py:142`](../../../.harness/scripts/qa_evidence_binding.py#L142)

- Review summary/digest/index context injection rather than full artifact replay.
  [`harness_context_index.py:39`](../../../.harness/scripts/harness_context_index.py#L39)

**Regression evidence**

- Follow the Story stage/root and legacy compatibility regression.
  [`test_harness_timing.py:93`](../../../tests/test_harness_timing.py#L93)

- Follow stale evidence and post-attempt tamper rejection.
  [`test_provider_attempt.py:66`](../../../tests/test_provider_attempt.py#L66)

- Confirm standalone block decisions return nonzero exit status.
  [`test_harness_cli_exit_contract.py:37`](../../../tests/test_harness_cli_exit_contract.py#L37)
