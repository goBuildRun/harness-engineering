# BMAD Planning 到 Work Item 的协同契约

本文定义 Team Product R&D Harness 如何把 BMAD Planning 产物连接到 Teambition、飞书任务、Jira 等 Work Item 系统。

> 多 provider 协同能力在精简方案中继续保留。目标状态由 `start/finish` 调用 adapter：本地 `finish pass` 最多同步到 ready/review，只有绑定 commit 的 CI 合并或发布成功后才能写入 `Done`。

## 1. 核心原则

**Work Item 不是 BMAD 产物仓库。**

| 内容 | 真相源 | Work Item 上的角色 |
|------|--------|--------------------|
| 产品规格、PRD、验收标准 | `harness-workspace/planning/product-specs/` | 摘要和链接 |
| 架构、执行计划、就绪检查 | `harness-workspace/planning/exec-plans/` | 风险和方案链接 |
| 任务包 `00-06`、DAG | `harness-workspace/planning/tasks/` | 任务目录链接和 Gate 状态 |
| Planning Gate 凭证 | `planning_gate_pass.json` | 状态记录 |
| QA、TEST、REVIEW、GROWTH | `harness-workspace/evidence/` | 关键结论和链接 |
| 负责人、排期、看板状态、讨论 | Teambition / 飞书 / Jira | 协同真相源 |

外部任务系统只承载：负责人、状态、讨论、轻量摘要和 Harness Links。完整 BMAD 文档必须留在产品仓库中，避免出现两个真相源。

## 2. 推荐粒度

| 级别 | Work Item 粒度 | 说明 |
|------|----------------|------|
| L1 | 可豁免或一个本地/轻量任务 | 小修补，不要求完整任务包 |
| L2 | 一个 Work Item 对应一个 product-spec、exec-plan 和 task package | 标准功能或单服务多文件 |
| L3 | 一个父 Work Item 加若干子任务 | 跨服务/API/DB/多端联动；父任务保留目标和总体验收，子任务承载领域执行 |

L3 即使在 Teambition/飞书/Jira 拆了子任务，执行真相仍以 `03-实施方案.md` 和 `tasks-dag.md` 为准。

## 3. 推荐协作流程

产品设计、产品经理或架构师在 Codex 中完成 BMAD Planning 后，不应马上把所有验收项写入外部任务系统。推荐流程是：

```bash
bash .harness/scripts/work_item.sh draft-spec \
  "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" \
  --assignee <provider-user-id>
```

`draft-spec` 只生成待确认任务草稿，不调用 Teambition、飞书或 Jira，也不会修改 Product Spec。人类在 Codex 对话中确认：

- 每个任务标题是否准确
- 范围是否只覆盖对应 Product Spec / 验收项
- 负责人是否正确
- L2 是否可单任务承载，L3 是否需要父子任务拆分

确认后再执行真实同步：

```bash
bash .harness/scripts/work_item.sh sync-spec \
  "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" \
  --assignee <provider-user-id>
```

`sync-spec --assignee` 优先级高于产品侧默认负责人和本机环境变量。同步成功后，开发人员或自动化 Agent 从 Teambition、飞书或 Jira 领取分派给自己的 Work Item，并通过 `agent_start.sh <work-item-id>` 进入 Harness Execution。

支持精确层级的 Provider（当前为飞书）要求 L3 Product Spec 声明 `work_item_type`。Story 的父 Work Item 可写在 front matter，或通过 CLI 显式传入；两者同时存在但不一致时同步会阻断，Epic 携带父级也会阻断：

```yaml
---
spec_level: L3
work_item_type: story
work_item_parent_id: <epic-work-item-id>
---
```

```bash
bash .harness/scripts/work_item.sh sync-spec \
  "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某-story.md" \
  --parent-id <epic-work-item-id>
```

Planning Gate、Harness Execution 或 QA 状态变化后，使用显式描述文件同步既有任务，不创建重复 Work Item：

```bash
bash .harness/scripts/work_item.sh update-description \
  --id <work-item-id> \
  --file <description.md>
```

`update-description` 会先校验任务存在，再更新完整描述；描述文件应保留 Product Spec、Exec Plan、Task Package、Context、Gate 和当前验收状态。当前实现支持飞书，且不会改变 `completed_at`。

## 4. bmad-work-item-v1

`work_item.sh sync-spec` 创建任务时使用 `bmad-work-item-v1` 描述契约。每个由产品规格勾选项生成的 Work Item 至少包含：

```md
## 目标
来自 Product Spec 的目标摘要。

## 范围
- In scope: `harness-workspace/planning/product-specs/xxx.md`
- Out of scope: Product Spec 中的非目标摘要

## Harness Links
- Product Spec: `harness-workspace/planning/product-specs/xxx.md`
- Exec Plan: `harness-workspace/planning/exec-plans/active/<待补>`
- Task Package: `harness-workspace/planning/tasks/<待 Planning Gate 后回填>`
- Context: `harness-workspace/knowledge/CONTEXT.md`

## Gate
- BMAD Planning: pending
- Planning Gate: pending
- Harness Execution: not_started
- Assignee: `<provider-user-id>`（如果同步时指定）

## 验收标准摘要
- [ ] 对应验收项
```

创建后，`sync-spec` 会把外部任务 ID 回写到 Product Spec 的验收项行尾：

```md
- [ ] 手机号登录成功后进入首页 #<work-item-id>
```

L2/L3 进入 Planning Gate 前，需要把这个 ID 填入 `00-任务卡.md` 的“任务编号”。Gate 不只校验 ID 存在：支持容器语义的 provider 还必须证明任务属于产品配置的项目/清单；声明了 `work_item_parent_id` 时，必须同时精确匹配父任务。

## 5. 状态语义

建议看板状态或标签采用这条链：

```text
Backlog
→ BMAD Planning
→ Planning Gate Ready
→ Harness Execution
→ QA Review
→ Merge / Release Accepted
→ Done
```

`Done` 是受控 Git 接收点或发布入口的接受状态，不是本地脚本成功的同义词。provider 不支持中间状态时可以只同步摘要和链接，但不能在有效 acceptance receipt 产生前提前完成任务。

如果协同系统无法自定义完整状态，用标签补齐：

```text
harness
bmad-planning
planning-gate-ready
harness-execution
qa-review
growth-review-required
```

当前 provider 能力边界：

| Provider | 创建任务 | 容器/父级绑定 | list-mine | 状态回写 |
|----------|----------|---------------|-----------|----------|
| `teambition` | 已支持 | 父子级适配未实现；显式父级会阻断 | 已支持 | `stage_id_map` 配置后支持 |
| `feishu` | 已支持 | 精确校验 tasklist、parent 和父级 tasklist；创建后有界重试回读 | 配置 tasklist/list_query 后支持 | 默认 skip；配置 `status_update_mode: completed` 后支持完成态回写 |
| `jira` | 已支持 | 父子级适配未实现；显式父级会阻断 | 已支持 | transition 配置后启用 |
| `noop` | 本地 ID | 本地格式与父级参数校验，`guarded` 且不调用外部 API | 空列表 | 本地 pass |

## 6. Teambition 管理建议

- 一个 L2 Work Item 对应一个产品规格验收项或一个标准功能。
- 用 Teambition 任务描述保留 `bmad-work-item-v1` 摘要和链接。
- `--assignee`、`TEAMBITION_ASSIGNEE_ID` 或产品侧 `providers.teambition.assignee_id` 可设置默认执行人。配置 `assignee_id` 后，Work Item Gate 会校验 Teambition raw `executorId == assignee_id`；只有执行者是当前 owner 的任务才能进入 Harness Execution，参与人不算。
- 如果项目区分“需求 / 任务 / 缺陷”等类型，产品侧必须配置“任务”类型的 `scenariofieldconfig_id`；Harness 的 `sync-spec/create` 会把它写入创建 payload，避免新 Work Item 落入“需求”工作流。可用 `work_item.sh scenario-configs --keyword 任务` 通过 Teambition v3 `scenariofieldconfig/search` 查询类型 ID；该查询需要 `Authorization: Bearer <appAccessToken>`、企业 ID `X-Tenant-Id` 和 `X-Tenant-Type: organization`。`appAccessToken` 可直接配置，也可由 Open App 的 App ID/Secret 按官方 JWT 规则本地签发。
- 产品侧推荐 `providers.teambition.status_update_mode: taskflowstatus`，`close` 会调用 Teambition Open API v3：先 `GET /api/v3/task/{taskId}/tfs` 查询任务所在工作流状态，再 `PUT /api/v3/task/{taskId}/taskflowstatus` 更新真实任务状态。可配置 `taskflowstatus_id_map` 或 `taskflowstatus_name_map` 显式指定状态。
- 兼容模式 `providers.teambition.status_update_mode: stage` 与 `stage_id_map` 仍可把 Harness 状态映射到 Teambition 看板阶段；`close` 会调用 `/v1.0/project/users/{uid}/tasks/{taskId}/stages`。
- `stage_id_map` / `taskflowstatus_id_map` 推荐 key：`pending`、`design`、`in_progress`、`testing`、`ready_to_release`、`done`、`implemented`、`cancelled`，分别对应“待处理 / 设计中 / 开发中 / 测试中 / 待发布 / 已完成 / 已实现 / 已取消”。
- 用任务评论记录 Planning Gate、QA、MR 的关键结论；评论不是状态流转的替代。
- Teambition 的 taskId 是 24 位十六进制 ID，必须填回 `00-任务卡.md`。

## 7. 飞书任务管理建议

- 产品侧 `project.yaml` 配置 `providers.feishu.tasklist_guid`，把任务创建到固定任务清单。
- 产品配置的 `tasklist_guid` 优先于兼容变量 `FEISHU_TASKLIST_GUID`；只有显式 `FEISHU_TASKLIST_GUID_OVERRIDE` 才能临时覆盖产品配置，避免通用 Harness 清单污染产品任务。
- L3 Story 声明 `work_item_type: story`，并使用 `work_item_parent_id` 或 `sync-spec --parent-id` 创建子任务；Epic 声明 `work_item_type: epic` 且不得有父级。飞书子任务自身可以没有 `tasklists` 字段，Harness 会精确校验 `parent_task_guid`，并证明父任务直接属于产品清单。即使子任务已直接加入正确清单，也不会跳过父 Epic 的清单验证。
- 如租户任务列表接口不同，可配置 `providers.feishu.list_tasks_path` 与 `providers.feishu.list_query`，`list-mine` 会拉取后按负责人本地过滤。
- `providers.feishu.assignee_id` 可配置产品默认负责人；个人本地覆盖用 `FEISHU_ASSIGNEE_ID`。
- `providers.feishu.status_update_mode: completed` 会把 Harness `done/closed/mr_merged` 映射为飞书任务 `completed_at=<当前毫秒时间戳>`；`in_progress/todo` 会清为 `"0"`，用于重新打开。Provider 必须在 PATCH 后回读并核对 canonical `done/open` 状态；当飞书只返回 `todo` 表示未完成时，结果保留 requested status 与真实 provider status 的区别。
- 飞书任务描述里只放摘要和链接，不复制完整 PRD。
- Webhook 需要按租户继续增强；短期用 `diagnose --id [--parent-id]` 校验清单和父级绑定，用 `verify` / `pull` 做存在性与原始数据排障。

## 8. Jira 管理建议

- 一个 Jira Issue 对应 L2 Work Item；L3 可用 Epic/Parent Issue 管总目标。
- `project_key`、`issue_type`、`assignee_id` 放产品侧 `project.yaml`；密钥放 `.env`。
- 状态流转依赖 Jira transition id，未配置前 `close` 只做 Harness 侧通过记录。

## 9. 禁止事项

- 不把完整 BMAD PRD、架构文档、执行计划复制到 Work Item。
- 不在 Work Item 上手工维护另一份验收标准真相源。
- 不用聊天记录替代 `planning/`、`knowledge/`、`evidence/`。
- 不让 L2/L3 绕过 Work Item ID 进入 Planning Gate。
