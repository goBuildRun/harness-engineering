# 文档沉淀边界

| 位置 | 放什么 |
|------|--------|
| **`ael-workspace/planning/tasks/`** | 单次任务过程：00–06、专项报告、`planning_gate_pass.json` |
| **`ael-workspace/planning/product-specs/`** | 验收标准、用户故事（BMAD Planning 映射） |
| **`ael-workspace/planning/exec-plans/`** | 跨任务执行计划（active / completed） |
| **`ael-workspace/runs/`** | Planning Gate 凭证、按 taskId 隔离的工作区 |
| **`ael-workspace/knowledge/`** | CONTEXT / LESSONS 长期知识 |
| **`ael-workspace/evidence/`** | INTAKE / SUMMARY / PROGRESS / TEST / REVIEW / GROWTH 证据 |
| `buildrun-agent-engineering-lifecycle/docs/` | AEL 规程与设计（**不含** product-specs） |
| `buildrun-agent-engineering-lifecycle/tasks/_templates/` | 任务卡**模板**（流程资产，非任务产出） |
| 产品配置的 architecture 路径 | 架构权威正文，**只链不抄** |
| active profile 声明的 migration 路径 | 数据迁移文件；具体目录、格式和规则由产品 profile 决定 |

BMAD Planning 产出根路径由产品 `ael-workspace/project.yaml` 配置（默认 `ael-workspace/planning/`）。

## 不应沉淀

- 聊天里的架构结论（须回写 `ael-workspace/planning/tasks/` 或产品配置的 architecture 路径）
- 把 `ael-workspace/evidence/intake-reports/` 直接当成长期知识（须人工 review 后迁入 `knowledge/` 或 architecture/）
- 在 `buildrun-agent-engineering-lifecycle/docs/product-specs/` 新建规格（已废弃，用 `ael-workspace/planning/product-specs/`）

## 任务结束后

长期有效内容 → 产品配置的 architecture 路径、`ael-workspace/knowledge/` 或 `ael-workspace/planning/exec-plans/completed/`；任务包保留在 `ael-workspace/planning/tasks/`。
