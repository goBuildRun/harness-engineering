# 协作法则：基于 Markdown 的 DAG 与 Subagent 调度

> **完整使用方式（命令逐步说明）**：[getting-started/cli.md](../../docs/getting-started/cli.md)

作为本工程的 Lead Agent（主智能体），你不能自己去写多仓库交叉的脏活累活。你是一个“包工头”。

## 1. 拆解 DAG (有向无环图)
当前 L2/L3 兼容任务在 `harness-workspace/planning/tasks/<task>/tasks-dag.md` 维护 DAG；目标统一入口按任务复杂度生成或物化最小待办。DAG 的实现节点必须与 `03-实施方案.md` ID 对齐，不能放在产品仓库根目录。

```markdown
### 任务流拓扑图
- [ ] T1: (服务名A) 任务简述 / 负责人: backend-agent / 前置依赖: 无
- [ ] T2: (服务名B) 任务简述 / 负责人: frontend-agent / 前置依赖: T1
```

当 execution tier 或风险触发时再增加 `T-QA`、`T-GC`、浏览器验证或人工 Gate 节点；不得为了套模板给低风险任务添加空仪式。

## 2. 计划门禁与状态驱动

调度子代理前，Lead 须跑 `feedback_planner.sh`，计划字符串须含：

- **测试/TDD** 关键词
- **`[ ]` 勾选语法**
- **active profile 允许的业务路径**或 `structure_guard`

示例：`bash .harness/scripts/feedback_planner.sh '[ ] T1 src/module/example.py 写失败测试 [ ] 实现 [ ] 沙箱验证'`

## 3. 风险匹配验证

- 所有实现任务都必须有可执行验证，且变更路径与任务契约一致。
- 要求独立 QA 的任务必须由 `qa-evaluator` 签字，并在成功调用 `.harness/scripts/subagent-pr-gate.sh T1` 后才能标记为 `[x]`。
- 未触发独立 QA 的任务由最终 Harness 结果证明合规，不生成虚假签章。

## 4. 条件收尾：Garbage Collection

- `strict`、跨模块重构、明显死代码或调试残留风险触发 `gc-sweeper` 与 `memory-sweep.sh`。
- `lite` / `standard` 在静态检查和 diff review 已覆盖熵减风险时可不启动独立 GC Agent。
- 无论是否独立 GC，最终检查都必须阻止调试残留、越界修改和未记录技术债进入有效完成态。

## 5. 沙箱隔离执行纪律 (Execution Environments)
- 任何构建与测试行为，必须被包装在沙箱管道内执行：`bash .harness/scripts/run_in_sandbox.sh "你的原生命令"`。违者将被安全系统剥夺执行权限。
