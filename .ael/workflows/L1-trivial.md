# L1 工序（BMAD Planning 简化 + Lifecycle Execution 简化）

> 定位：当前兼容工序。L1 是 planning level，不等于目标 `lite` execution tier。当前可豁免外部 Work Item；目标状态仍由 `start` 生成可追踪的 AEL 本地任务 ID。

## BMAD Planning

- `planning/product-specs/<功能>.md` 或 context
- 级别确认为 L1
- `bash .ael/scripts/planning_gate.sh L1`
- 产出：`runs/planning_gate_pass.json`（兼容写入 `phase0_pass.json`；通常无 `planning/tasks/` 目录）

## Lifecycle Execution

- `bash .ael/scripts/agent_start.sh l1-local`（占位参数；跳过 work_item 校验）
- 轻量 `tasks-dag.md`（可单任务）
- `feedback_planner.sh` 须含 **测试关键词 + `[ ]` + active profile 允许的业务路径**
- `structure_guard.sh --path` / `--diff`；`plan_sync_check.sh`（有业务 diff 时）
- `run_in_sandbox.sh`
- 独立 QA 可按 execution tier 省略；最终验证和 AEL 合规结果不可省略
- GC 按需

## 升级

命中跨服务/契约/DB → planning level 升到 L3，并把 execution tier 至少升级到风险规则要求；回到 BMAD Planning 补 `planning/tasks/` 与 Work Item。二者都只能升级，不能用“原为 L1”维持低验证深度。
