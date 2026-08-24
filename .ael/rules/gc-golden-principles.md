# GC 黄金原则（熵减机械规则）

> **memory-sweep 使用方式**：[getting-started/cli.md](../../docs/getting-started/cli.md) 第 6 节。

> 供 `gc-sweeper` 与 `memory-sweep.sh` 扫描依据。人类品味一旦写入此处，即持续应用于全库。

## 必须移除

| 模式 | 说明 |
|------|------|
| `TODO: AI` / `FIXME: agent` / `AI fixed` | 无意义过程注释 |
| `console.log` / `println!` / 裸 `print(` | 调试输出（测试除外） |
| 未使用的 import / 死函数 | 由静态分析或人工确认后删除 |

## 必须保留

- 有 ticket/任务 ID 的 `TODO`
- 结构化日志（非 stdout 调试）
- 测试中的断言与 fixture 输出

## 偏好

1. 共享 util 包优于复制粘贴 helper
2. 单文件 ≤ 400 行（超出拆分或记技术债）
3. 重复第三次出现的逻辑 → 提取并补测试

## 签退

`finish` 每次执行 diff 机械扫描，并统一决定是否调用独立 GC。有效 `gc_result.json` 必须同时声明 `role: gc-sweeper`、`independent: true`，并绑定当前 `task_id`、`subject_digest` 和 `policy_digest`；缺任一项都返回 `GC_REQUIRED`。不得依赖 Lead 或 Agent 记住手工步骤。GC 修改代码后，旧测试、结构和 code-health fingerprint 失效并重新验证。

机械扫描负责召回风险，独立 GC 负责最终判定。命中 `debug_output` 或 `large_file` 不代表必须删除结构化命令输出或强拆职责内聚文件；有效、独立且绑定当前 subject/policy 的 GC pass 可以裁决这些误报，结果必须同时保留原始 `mechanical_decision`。GC receipt 无效、返回 block 或留下未绑定 Work Item 的 deferred finding 时仍 fail closed。

GC 默认上下文只包含任务契约、`changed_since_baseline` 文件的真实 patch/新增文件内容、变更文件列表和一层直接依赖。超过 `AEL_GC_CONTEXT_MAX_CHARS` 预算时返回 `BUDGET_APPROVAL_REQUIRED`，禁止静默截断证据或无条件读取全仓。
