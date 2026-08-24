# Planning 产出目录

本目录存放 **BMAD Planning** 的可交付物。产品侧目录使用 `planning/`，与 buildrun-agent-engineering-lifecycle runtime（脚本、门禁、CI、工作流文档）分离。

项目级长期知识位于 `{{workspace}}/knowledge/`，已有项目接入报告、任务证据与成长报告位于 `{{workspace}}/evidence/`，让团队不依赖聊天记忆。

## 配置

路径由产品根相对配置，见生成后的 `{{workspace}}/project.yaml`：

| 配置键 | 默认相对路径 | 说明 |
|--------|----------------|------|
| `workspace.planning` | `planning` | BMAD Planning 产物根目录 |
| `planning.product_specs` | `product-specs` | PRD / 验收标准 |
| `planning.exec_plans_active` | `exec-plans/active` | 进行中执行计划 |
| `planning.exec_plans_completed` | `exec-plans/completed` | 已完成执行计划 |
| `planning.tasks` | `tasks` | 任务包（`YYYY-MM-DD-*`） |
| `workspace.runs` | `runs` | 运行状态、凭证、按 Work Item ID 隔离工作区 |
| `workspace.knowledge` | `knowledge` | CONTEXT / LESSONS |
| `workspace.evidence` | `evidence` | INTAKE / SUMMARY / PROGRESS / TEST / REVIEW / GROWTH |
| `bmad.install_root` | `.` | BMAD Method 安装目录（产品根 `_bmad/`） |
| `bmad.output_root` | `ael-workspace/bmad-output` | BMAD 原生输出暂存区，含 planning/design/implementation/test artifacts |
| `bmad.normalized_planning_root` | `ael-workspace/planning` | 映射后的正式 BMAD Planning 产物目录 |

解析脚本：buildrun-agent-engineering-lifecycle 的 `.ael/scripts/workspace_paths.py`。
初始化脚本：在 buildrun-agent-engineering-lifecycle 目录执行 `bash .ael/scripts/ael_init.sh init ...`。

## 子目录

| 路径 | 内容 |
|------|------|
| `product-specs/` | 产品规格与索引 |
| `exec-plans/active/` | 活跃执行计划 |
| `exec-plans/completed/` | 已归档执行计划 |
| `tasks/` | 按日期和 work item 命名的任务目录 |
| `{{workspace}}/knowledge/CONTEXT.md` | 项目共享上下文 |
| `{{workspace}}/knowledge/LESSONS.md` | 跨任务失败教训 |
| `{{workspace}}/knowledge/REFERENCE_SYSTEMS.md` | 上游与重度依赖系统的核心逻辑、关键代码入口、扩展点和禁改边界 |
| `{{workspace}}/evidence/` | intake-reports/summaries/progress/test-reports/review-reports/growth-reports |
| `{{workspace}}/runs/` | 门禁与工作区状态（勿手改除非调试） |

任务卡模板位于 buildrun-agent-engineering-lifecycle 的 `tasks/_templates/`；Lifecycle Execution 写码规程见 buildrun-agent-engineering-lifecycle `docs/getting-started/cli.md`。
