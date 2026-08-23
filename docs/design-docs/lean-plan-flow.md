# Lean Planning Flow

状态：已实现第一版，作为 `harness plan` 的规范说明。

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

## 运行规则

- 全局前置检查和共享 BMAD/Architecture/Readiness digest 每个 batch 只做一次。
- 子项凭证按任务目录和依赖 digest 失效；无关历史文件不会使整个 batch 重跑。
- Provider create/readback/binding 在同一个 receipt 中记录；适配器已有完整 readback 时不得再次 `pull`。
- `harness start` 默认写 `runs/tasks/<id>/`，多个任务不会覆盖彼此。旧全局 active 文件只为历史命令兼容。
- planning level 与 execution tier 正交。新 batch 根据实际 scope/risk 选择 `lite`、`standard` 或 `strict`；L3 不再无条件映射 strict。高风险路径仍自动升级。
- Growth/document aggregation 不在关键路径；只有明确 release blocker 时才加入 finish gate。`HARNESS_LEAN_FLOW=1` 会跳过无触发的 `growth_release`。
- 知识同步使用输入 digest cache；命中缓存时只返回 receipt，不重写 `CONTEXT.md`。

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

