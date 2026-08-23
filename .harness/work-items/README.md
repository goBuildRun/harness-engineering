# Work Item 协同层（产品级可替换适配器）

Team Product R&D Harness **不绑定**某一个协同系统。BMAD Planning 与 Harness Execution 通过 **Work Item 抽象层** 对接外部任务系统。

Work Item 的职责是协同，不是保存 BMAD 产物本体。产品规格、执行计划和任务包的真相源始终是产品仓库的 `harness-workspace/planning/`；Teambition、飞书、Jira 只保存负责人、状态、讨论、摘要和链接。完整契约见 [../../docs/BMAD_Work_Item_Contract.md](../../docs/BMAD_Work_Item_Contract.md)。

> 目标状态由 `harness start/finish` 绑定并同步 Work Item：本地 `finish` 最多进入 ready/review，只有受控 Git 接收点或发布入口接受目标 commit 后才能写 `done`。下面 `work_item.sh` 是当前 adapter 与兼容命令参考，不是目标公开操作面。

同一个 harness-engineering 可以同时管理多个产品：

| 产品 | 产品侧配置 | Work Item provider |
|------|------------|--------------------|
| A 项目 | `A/harness-workspace/project.yaml` | `teambition` |
| B 项目 | `B/harness-workspace/project.yaml` | `feishu` |
| C 项目 | `C/harness-workspace/project.yaml` | `jira` |

## 配置分工

长期选择写在产品侧：

```yaml
work_item:
  provider: noop   # noop | teambition | feishu | jira
  id_pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"
```

本机临时覆盖写在环境变量：

```bash
WORK_ITEM_PROVIDER=feishu
WORK_ITEM_ID_PATTERN='^[A-Za-z0-9_-]{8,128}$'
```

Harness 默认配置在 `.harness/work-items/config.yaml`，只提供 provider 默认值、默认 API host 和兼容策略。

产品侧 `harness-workspace/project.yaml` 可以保存非密钥配置，例如：

- Teambition：`project_id`、可选 `assignee_id`、`scenariofieldconfig_id`、`status_update_mode`、`stage_id_map`、`taskflowstatus_id_map`
- 飞书：`tasklist_guid`、可选 `assignee_id`
- Jira：`base_url`、`project_key`、`issue_type`、可选 `assignee_id`

密钥和个人身份必须来自环境变量、`.env` 或 Secret Manager，不要写入产品仓库：

- Teambition：`DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_OPERATOR_USER_ID`
- 飞书：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`
- Jira：`JIRA_EMAIL`、`JIRA_API_TOKEN`

严格规则：外部 provider（Teambition / 飞书 / Jira）缺少密钥时会 `block`，不会自动退回本地 ID。需要离线或本地演示时，必须显式把产品或环境变量切到 `noop`。

## 初始化产品时指定 provider

```bash
bash .harness/scripts/harness_init.sh init \
  --product-root /path/to/product-a \
  --product-id product-a \
  --product-name "Product A" \
  --profile generic \
  --work-item-provider teambition

bash .harness/scripts/harness_init.sh init \
  --product-root /path/to/product-b \
  --product-id product-b \
  --product-name "Product B" \
  --profile generic \
  --work-item-provider feishu

bash .harness/scripts/harness_init.sh init \
  --product-root /path/to/product-c \
  --product-id product-c \
  --product-name "Product C" \
  --profile generic \
  --work-item-provider jira
```

## ID 规范

Work Item ID 由 provider 决定：

| Provider | 默认 ID 形态 | 示例 |
|----------|---------------|------|
| `noop` | 语义化本地 ID | `policy-guardrails-provider-poc` |
| `teambition` | 24 位 hex taskId | `6a335eb127a6242c96a84cb5` |
| `feishu` | 飞书 task guid | `task_guid_xxx` |
| `jira` | Jira issue key | `PROJ-123` |

写入位置：`00-任务卡.md`、`planning_gate_pass.json`、`product-specs` 勾选行尾 `#<work-item-id>`。`phase0_pass.json` 仅作为历史兼容副本同步写入。

## BMAD → Work Item 同步契约

`draft-spec` / `sync-spec` 使用 `bmad-work-item-v1`：

- `draft-spec` 从 `harness-workspace/planning/product-specs/<功能>.md` 的未绑定验收勾选项生成待确认任务草稿，不调用外部 API，也不修改 Product Spec。
- 人在 Codex 对话中确认任务标题、范围和负责人。
- `sync-spec` 创建 Work Item，在描述中写入目标、范围、负责人、Harness Links、Gate 状态和验收摘要。
- `sync-spec` 在 Product Spec 勾选项行尾回写 `#<work-item-id>`。
- 不复制完整 PRD、架构文档或执行计划到外部任务系统。

建议外部看板状态链：

```text
待处理 → 设计中 → 开发中 → 测试中 → 待发布 → 合并/发布已接受 → 已完成
```

## L1/L2/L3 策略

L1/L2/L3 是当前 planning level。目标 execution tier 另使用 `lite/standard/strict`，由实际变更风险决定：

| 级别 | 须绑定 Work Item |
|------|------------------|
| L1 | 否，单文件修补可豁免 |
| L2 | 是，按当前产品 provider 校验 |
| L3 | 是，按当前产品 provider 校验 |

## 命令

```bash
bash .harness/scripts/work_item.sh diagnose
bash .harness/scripts/work_item.sh diagnose --id <work-item-id>
bash .harness/scripts/work_item.sh capabilities
bash .harness/scripts/work_item.sh scenario-configs --keyword 任务
bash .harness/scripts/work_item.sh draft-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" --assignee <provider-user-id>
bash .harness/scripts/work_item.sh sync-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" --assignee <provider-user-id>
bash .harness/scripts/work_item.sh verify --id <work-item-id> --level L2
bash .harness/scripts/work_item.sh pull --id <work-item-id>
bash .harness/scripts/work_item.sh list-mine
# 普通调用只允许推进到待发布；本地 done 会固定阻断
bash .harness/scripts/work_item.sh close --id <work-item-id> --status ready_to_release
```

## Teambition / 钉钉

产品配置：

```yaml
work_item:
  provider: teambition
  id_pattern: "^[0-9a-f]{24}$"
  providers:
    teambition:
      project_id: "6a3347ec5b0821fb32fafefd"
      assignee_id: ""  # 可选：产品默认执行者；配置后 Work Item Gate 只允许 executorId 等于该值的任务进入执行
      tenant_id: "<企业 ID>"
      tenant_type: organization
      open_app_id: "<Teambition Open App ID>"
      scenariofieldconfig_id: "<任务类型 scenariofieldconfigId>"
      status_update_mode: taskflowstatus  # skip | stage | taskflowstatus
      stage_id_map:
        pending: "<待处理 stageId>"
        design: "<设计中 stageId>"
        in_progress: "<开发中 stageId>"
        testing: "<测试中 stageId>"
        ready_to_release: "<待发布 stageId>"
        done: "<已完成 stageId>"
        implemented: "<已实现 stageId>"
        cancelled: "<已取消 stageId>"
      taskflowstatus_id_map:
        done: "<已完成 taskflowstatusId>"
      taskflowstatus_name_map:
        done: "已完成"
```

本机 `.env`：

```bash
DINGTALK_APP_KEY=
DINGTALK_APP_SECRET=
DINGTALK_OPERATOR_USER_ID=
# 可选：open.teambition.com v3 appAccessToken，用于只读查询类型配置。
# 二选一：
# 1. 直接提供 Authorization，格式为 Bearer <access_token>
TEAMBITION_OPEN_API_AUTHORIZATION=
# 2. 或提供 Open App 凭证，Harness 会按官方 JWT 规则本地签发 appAccessToken
TEAMBITION_OPEN_APP_ID=
TEAMBITION_OPEN_APP_SECRET=
TEAMBITION_TENANT_ID=
# 可选：仅用于临时覆盖产品侧 project_id / assignee_id
TEAMBITION_PROJECT_ID=
TEAMBITION_ASSIGNEE_ID=
# 可选：临时覆盖产品侧 scenariofieldconfig_id
TEAMBITION_SCENARIOFIELDCONFIG_ID=
# 可选：临时覆盖产品侧 stage_id_map
TEAMBITION_STAGE_ID_DONE=
# 可选：临时覆盖产品侧 taskflowstatus_id_map / taskflowstatus_name_map
TEAMBITION_TASKFLOWSTATUS_ID_DONE=
TEAMBITION_TASKFLOWSTATUS_NAME_DONE=
# Open API v3 更新任务状态的操作者；必须是 Teambition/Open API 用户 ID，不是钉钉 userId
TEAMBITION_OPEN_OPERATOR_ID=
```

注意：`DINGTALK_OPERATOR_USER_ID` 是钉钉通讯录 userId，不是 Teambition 24 位任务 ID，也不是 Open API v3 的 `X-Operator-Id`。使用 `status_update_mode: taskflowstatus` 时，请配置 Teambition/Open API 用户 ID 到 `TEAMBITION_OPEN_OPERATOR_ID` 或产品侧 `open_operator_id`。
如果项目中同时存在“需求”和“任务”类型，`sync-spec` 应配置“任务”类型的 `scenariofieldconfig_id`；否则可能创建到“需求”工作流，导致 `close --status done` 行为和任务型 Work Item 不一致。

当前 Teambition 适配器支持：

- `diagnose`：校验钉钉 token、操作者 userId 和项目任务列表访问
- `verify` / `pull`：按 Teambition taskId 读取任务
- `scenario-configs --keyword 任务`：调用 `https://open.teambition.com/api/v3/scenariofieldconfig/search` 查询“任务/需求/缺陷”等类型配置；官方参数为 `q` / `sfcIds` / `pageToken` / `pageSize`，Header 需要 `Authorization: Bearer <appAccessToken>`、`X-Tenant-Id` 和 `X-Tenant-Type: organization`。`appAccessToken` 可直接配置，也可由 `TEAMBITION_OPEN_APP_ID/SECRET` 本地签发 JWT。
- `draft-spec`：生成待确认任务草稿，不写 Teambition
- `sync-spec --assignee <id>`：创建任务、设置执行者并回写 `#<taskId>`
- `verify` / Planning Gate：若配置了 `assignee_id` 或 `TEAMBITION_ASSIGNEE_ID`，必须满足 Teambition raw `executorId == assignee_id` 才允许进入 Harness Execution；参与人不算执行者。
- `list-mine`：优先拉取项目任务并按执行者 `executorId` 过滤；若 Teambition 列表接口漏返当前产品已同步的 Work Item，则从 `harness-workspace/planning/product-specs/` 的未勾选 `#taskId` 逐个 `pull` 兜底，并过滤 `isDone=true`
- `close`：推荐 `status_update_mode: taskflowstatus`，调用 `GET /api/v3/task/{taskId}/tfs` 发现任务工作流状态，再 `PUT /api/v3/task/{taskId}/taskflowstatus` 更新真实任务状态；也可用 `taskflowstatus_id_map` 或 `taskflowstatus_name_map` 显式指定。兼容模式 `status_update_mode: stage` 仍调用 `/v1.0/project/users/{uid}/tasks/{taskId}/stages` 移动看板阶段。

Harness 状态到 Teambition 阶段的默认映射：

| Harness status | `stage_id_map` key | Teambition 阶段 |
|----------------|--------------------|-----------------|
| `pending` / `todo` | `pending` | 待处理 |
| `bmad_planning` / `planning_gate_ready` | `design` | 设计中 |
| `in_progress` / `harness_execution_started` | `in_progress` | 开发中 |
| `qa` / `qa_passed` | `testing` | 测试中 |
| `ready_to_release` | `ready_to_release` | 待发布 |
| `done` / `closed` / `mr_merged` | `done` | 已完成 |
| `implemented` | `implemented` | 已实现 |
| `cancelled` / `canceled` | `cancelled` | 已取消 |

## 飞书任务

产品配置：

```yaml
work_item:
  provider: feishu
  id_pattern: "^[A-Za-z0-9_-]{8,128}$"
  providers:
    feishu:
      api_host: https://open.feishu.cn
      tasks_path: /open-apis/task/v2/tasks
      list_tasks_path: /open-apis/task/v2/tasks
      list_query: {}
      tasklist_guid: ""
      assignee_id: ""  # 可选：产品默认负责人；本次覆盖用 --assignee
      status_update_mode: skip  # skip | completed | description
```

本机 `.env`：

```bash
FEISHU_APP_ID=
FEISHU_APP_SECRET=
# 兼容默认：仅在产品侧未配置 tasklist_guid 时使用
FEISHU_TASKLIST_GUID=
# 显式临时覆盖产品侧 tasklist_guid
FEISHU_TASKLIST_GUID_OVERRIDE=
# 可选：临时覆盖产品侧 assignee_id
FEISHU_ASSIGNEE_ID=
# 可选：仅用于一次性初始化清单协作者。必须来自清单 owner/editor 用户授权。
FEISHU_USER_ACCESS_TOKEN=
```

当前飞书适配器支持：

- `diagnose`：校验 tenant access token
- `diagnose`：若产品配置了 `tasklist_guid`，同时校验应用是否有读取该任务清单的权限
- `feishu-tasklist-member`：把当前 `FEISHU_APP_ID` 作为 `app/editor` 添加到 `tasklist_guid`；如果 tenant token 还没有清单编辑权，请通过 `FEISHU_USER_ACCESS_TOKEN` 或 `--user-access-token` 使用清单 owner/editor 的用户授权执行一次
- `diagnose --id <task_guid>`：校验 tenant access token，并只读校验任务直接属于产品清单，或通过父任务继承该清单
- `diagnose --id <task_guid> --parent-id <parent_guid>`：额外精确校验父任务绑定
- `diagnose --create-smoke-title "Harness smoke"`：显式创建一个 smoke 任务，用于真实写入联调；默认 diagnose 不写飞书
- `verify` / `pull`：按 task guid 读取任务
- `draft-spec`：生成待确认任务草稿，不写飞书
- `sync-spec --assignee <id>`：创建任务、设置负责人、回读校验容器并回写 `#<task_guid>`
- `sync-spec --parent-id <parent_guid>`：创建子任务并精确回读父级；也可在 Product Spec front matter 写 `work_item_parent_id`。飞书 L3 Spec 还须声明 `work_item_type: story|epic|task`；Story 必须有父级，Epic 禁止有父级
- `list-mine`：默认用 `tasklist_guid` 调用 `/open-apis/task/v2/tasklists/{tasklist_guid}/tasks`，也可用 `list_query` / `list_tasks_path` 适配租户差异，并在本地按负责人过滤
- `close`：普通 CLI 只允许非终态；`close --status done` 固定返回 `ACCEPTANCE_AUTHORITY_SERVICE_REQUIRED`，不会调用 provider。受控 release-gate 服务验证固定 `TrustPolicy`、receipt、exact ref tip 和关闭顺序后，才可调用 Feishu provider 的终态更新；Jira/Teambition 终态写入未纳入该受控路径。产品侧配置 `status_update_mode: completed` 时，非终态 `in_progress/todo/ready_to_release` 会清除 `completed_at` 为 `"0"`；飞书把这些状态都表示为未完成，Harness 不会把 provider 的 `todo` 冒充成精确阶段。

创建任务或子任务后，Provider 会在有限次数内回读并校验 task GUID、产品清单和父级。读回耗尽时错误会保留 `created_id` 供人工恢复，避免重复创建；多验收项同步会在每次创建成功后立即回写对应 ID，后续项失败不会丢失已创建项的本地绑定。

推荐飞书接入测试顺序：

```bash
# 1. 离线契约测试，不需要真实飞书凭证
python3 -m unittest tests.test_feishu_provider

# 2. 真实凭证只测 token
WORK_ITEM_PROVIDER=feishu bash .harness/scripts/work_item.sh diagnose

# 2.1 如清单 diagnose 返回 1470403，先由清单 owner/editor 的 user token 把 app 加为清单 editor
FEISHU_USER_ACCESS_TOKEN=<user_access_token> \
WORK_ITEM_PROVIDER=feishu bash .harness/scripts/work_item.sh feishu-tasklist-member

# 3. 只读校验一个已有飞书任务
WORK_ITEM_PROVIDER=feishu bash .harness/scripts/work_item.sh diagnose --id <task_guid>

# 4. 显式写入 smoke 任务（确认要在飞书里创建任务时才执行）
WORK_ITEM_PROVIDER=feishu bash .harness/scripts/work_item.sh diagnose --create-smoke-title "Harness smoke"
```

## Jira / 其它系统

产品配置：

```yaml
work_item:
  provider: jira
  id_pattern: "^[A-Z][A-Z0-9]+-[0-9]+$"
  providers:
    jira:
      base_url: https://your-domain.atlassian.net
      project_key: PROJ
      api_version: "3"
      issue_type: Task
      assignee_id: ""  # 可选：Jira accountId；本次覆盖用 --assignee
      status_update_mode: skip
```

本机 `.env`：

```bash
# 可选：仅用于临时覆盖产品侧 base_url
JIRA_BASE_URL=
JIRA_EMAIL=
JIRA_API_TOKEN=
# 可选：仅用于临时覆盖产品侧 project_key
JIRA_PROJECT_KEY=
JIRA_ASSIGNEE_ID=
JIRA_ISSUE_TYPE=
JIRA_DONE_TRANSITION_ID=
```

当前 Jira 适配器支持：

- `diagnose`：校验 Jira API 可访问
- `verify` / `pull`：按 issue key 读取 issue
- `draft-spec`：生成待确认 issue 草稿，不写 Jira
- `sync-spec --assignee <accountId>`：创建 issue、设置负责人并回写 `#PROJ-123`
- `list-mine`：按 `assignee = currentUser()` 查询未完成 issue，可用 `JIRA_LIST_MINE_JQL` 覆盖
- `close`：默认只本地返回 pass，不移动 Jira 状态；配置 transition id 后再启用真实状态流转

其它系统新增 provider 时实现 `verify / pull / create / update_status / list_assigned`，并注册到 `work_item_providers.py` 的 `REGISTRY`。
