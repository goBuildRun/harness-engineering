# Lean Planning Flow

状态：已实现；离线闭环已验证，下一次真实 strict/L3 Story 试运行仍保持 `pending`。

## 目标

同等级 Story 的公开操作面收敛为：

```text
harness plan -> harness start <work-item-id> -> harness finish <task-id>
                                      \-> harness status <task-id>（只观察）
```

规划、Work Item 绑定、Planning Gate、上下文同步和任务凭证仍然存在，但由 batch receipt 编排，不要求使用者逐项运行 `draft-spec`、`sync-spec`、`confirm`、`planning_gate` 和 `activate`。

## `harness plan`

```bash
harness --product-root "$PRODUCT_ROOT" plan \
  --level L3 \
  --task-dir "$PRODUCT_ROOT/harness-workspace/planning/tasks/<task-dir>"
```

默认 `--provider-mode offline`，只生成本地 planning bundle；未确认外部容器身份时不得猜测或创建远端任务。明确授权后才使用 `--provider-mode configured`。每个 batch 写入：

```text
harness-workspace/runs/planning/<batch-id>/
├── planning-bundle.json
├── planning-ledger.jsonl
├── provider-readback.json
└── children/<work-item-id>.json
```

child receipt 保存独立 Work Item ID、任务目录、范围 digest、Planning Gate 和 provider mode。它是执行阶段的输入，不覆盖共享 `runs/active_task.json`。

`batch-id` 默认由 level、共享规划 digest 和任务目录集合确定，同一输入重复执行会直接返回 `cache_hit: true`。child digest 只包含任务包源文件，排除 Harness 生成的 `task.json` / gate 副本；单个任务变化只使该 child 失效。

offline 模式会生成确定性的本地 Work Item ID 并写入任务包 `task.json`，不调用外部系统。`--provider-mode configured` 必须对应产品已配置的 provider；缺少任务 ID 时才创建 Work Item，创建响应、一次性 readback、项目/Tasklist 和父级 binding 验证会写入同一 child receipt，任何歧义都 fail closed。

## 运行规则

- 全局前置检查和共享 BMAD/Architecture/Readiness digest 每个 batch 只做一次。
- 共享 receipt 包含 BMAD Method 结果摘要、Architecture 输入 digest 和 Implementation Readiness digest；不完整 BMAD 产物保留 `guarded/pending`，最终 Planning Gate 仍是执行准入权威。
- 子项凭证按任务目录和依赖 digest 失效；无关历史文件不会使整个 batch 重跑。
- Provider create/readback/binding 在同一个 receipt 中记录；适配器已有完整 readback 时不得再次 `pull`。
- `harness start` 默认写 `runs/tasks/<id>/`，多个任务不会覆盖彼此。旧全局 active 文件只为历史命令兼容。
- planning level 与 execution tier 正交。新 batch 根据实际 scope/risk 选择 `lite`、`standard` 或 `strict`；L3 不再无条件映射 strict。高风险路径仍自动升级。
- Growth/document aggregation 不在关键路径；只有明确 release blocker 时才加入 finish gate。`HARNESS_LEAN_FLOW=1` 会跳过无触发的 `growth_release`。
- batch `start` 将 lean 策略、Growth 触发策略和 task-scoped 身份持久化到 `result.json`；独立进程执行 `finish` 会恢复同一策略，不会因环境变量丢失而重复 Growth gate。
- independent QA 由一个 bounded fan-out 并行校验 T1-T5（或任务包中的等价 QA 单元），结果按 task ID 稳定聚合；每个独立 reviewer receipt、TEST/REVIEW digest 仍分别保留。
- 知识同步使用输入 digest cache；命中缓存时只返回 receipt，不重写 `CONTEXT.md`。

完整离线基准：

```bash
python3 .harness/scripts/story_cycle_benchmark.py \
  --fixture tests/fixtures/story-43-5-efficiency.json
```

该命令同时报告 gate 并行/精确缓存和六阶段完整生命周期的 before/after；生产 P95、Judge、真实 Agent active time 继续输出 `unknown`。

## 时间预算

| 阶段 | 默认预算 |
|------|----------|
| global preflight | 1 分钟 |
| shared planning | 3 分钟 |
| start / lifecycle preflight | 1 分钟 |
| 实现与定向测试 | 10 分钟 |
| 并行独立 QA 与必要修复 | 7 分钟 |
| Provider preflight、必要的单次真实验收、GC/finish/commit/readback | 5 分钟 |
| 缓冲 | 3 分钟 |

任一阶段超时必须返回结构化 `STAGE_BUDGET_EXCEEDED`，并给出 `stage`、`next_action: stop_and_escalate`；禁止无界重试或静默继续返工。

## 质量边界

精简只减少重复编排，不减少 L0/L1 安全、真实性、隐私、隔离、独立 QA、生产验收或 guarded evidence。真实 Provider 只在本地 preflight 通过后调用一次；未知 Token、P95、Judge、L3 和遥测仍保持 `unknown`/`pending`。
