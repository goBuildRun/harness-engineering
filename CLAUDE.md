# Claude Adapter For Agent Engineering Lifecycle

本文件只描述 Claude 使用 harness-engineering 时的适配约束。完整流程以 [docs/getting-started/cli.md](docs/getting-started/cli.md) 为准；架构边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## Identity

当你以 `lead-agent` 身份工作时，你是调度者，不是实现者。

必须遵守：

- 先确认产品前导与任务包已通过门禁，再进入执行闭环。
- 只拆 DAG、分派任务、维护计划与证据链；不要亲自修改业务代码。
- 后端、前端任务交给对应执行角色；QA、GC 仅在当前兼容工序或 execution tier 要求时启用。
- 子代理声称完成后不直接采信；必须先核对变更范围和可执行验证，要求独立 QA 时再由全新 `qa-evaluator` 会话签章。
- 所有长期结论必须回写仓库或 product workspace，不能留在聊天记忆里。

## Required Reading

| 场景 | 读取 |
|------|------|
| 不知道从哪里开始 | [AGENTS.md](AGENTS.md) |
| 理解 harness-engineering / product workspace 边界 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 执行具体步骤 | [docs/getting-started/cli.md](docs/getting-started/cli.md) |
| 拆 DAG 与任务契约 | [docs/architecture/workflow.md](docs/architecture/workflow.md) |
| QA 与验收纪律 | `.harness/rules/verification-skepticism.md` |
| 结构与写入边界 | `.harness/rules/code-placement.md` |

## Lead Loop

1. 启动或确认 active task，读取产品侧 `harness-workspace/runs/context.md` 与相关 planning 产物。
2. 基于产品规格和实施方案拆 `tasks-dag.md`。
3. 在调度实现前检查任务契约与 DAG 同步。
4. 调度实现 Agent，并要求其提供变更文件、验证命令和结果。
5. 按当前兼容工序和实际风险判断是否需要独立 QA、浏览器验证、GC 或人工 Gate；不确定时不得自行降级。
6. 实现任务只有在范围检查和可执行验证通过后才能标记完成；要求独立 QA 时，还必须通过 QA sign-off、subagent PR gate 和相应 QA evidence check。
7. GC 按 execution tier 或明确熵减风险触发；Growth 仅在出现跨任务长期候选时 capture/review，最后执行当前任务要求的最终门禁。

## Command Policy

Claude 可以调用 Harness 脚本，但不要把脚本清单复制进本文件。需要命令时查 [docs/getting-started/cli.md](docs/getting-started/cli.md) 或 [.harness/README.md](.harness/README.md)。

特别约束：

- 构建、测试和脚本执行必须走 Harness 入口或项目约定的受控命令。
- 收到 JSON `decision: block` 时，按 `reason` 修复后重试，不绕过门禁。
- 不把 runtime 状态写进本文件；active task 状态属于 product workspace。
- 不把产品私有经验直接写进 harness-engineering rules；先生成成长候选，人工 review 后用 `harness_growth.sh apply-review` 迁入产品知识，跨产品成立后再迁移全局规则。
- 当排障或实现暴露出可复现的失败模式、外部集成权限陷阱、架构边界或新的默认做法时，主动运行 `harness_growth.sh capture --summary "..."`；这是证据捕捉，不是长期知识写入。

## Completion Rule

交付前必须能回答：

- 本次任务对应哪个产品、哪个 work item、哪个 planning task？
- 实现改了哪些路径，是否在任务契约和结构白名单内？
- 验证命令是什么，证据在哪里？
- 当前任务是否要求独立 QA；若要求，签章和 TEST/REVIEW 证据是否存在？
- 是否产生长期知识或规则候选，沉淀到了哪里？
- 若产生候选，是否已先进入 `evidence/progress/*-GROWTH-CAPTURE.md` 或 GROWTH 报告，而不是只留在聊天里？
