# Team Product R&D Harness 多人协作手册

> **定位**：多 PM 并行设计、多开发并行执行时的协作约定与命令。  
> **全景上下文**：[Harness_全景手册.md](./Harness_全景手册.md)  
> **使用说明**：[USAGE.md](./USAGE.md)
> **精简目标**：多人隔离、角色边界和 provider 协同继续保留；推荐使用者调用 `plan/start/finish`，`status` 只观察，现有 `task_workspace`、provider 和 QA 命令收为内部能力。

---

## 1. 核心结论（先读）

| 问题 | 答案 |
|------|------|
| 多个 PM 同时在协同系统建任务？ | ✅ 支持，各用分支 + 独立 `harness-workspace/planning/tasks/` 目录；provider 可为 Teambition、飞书或 Jira |
| 已有项目首次接入由谁负责？ | 建议 Tech Lead + PM + QA 共同 review `intake-reports/`，再决定哪些事实进入知识库 |
| 任务分给多人，每人要一套 Harness 吗？ | ❌ **共享一套 Harness 规程**；每人 **一个任务工作上下文** |
| 需要每人 clone 一个仓库吗？ | ❌ 同一 monorepo；**每人一任务一分支** |
| 能否两人同时做两个任务？ | ✅ 可以；每个终端先固定自己的 `HARNESS_PRODUCT_ID` / `HARNESS_PRODUCT_ROOT`，产品内工作区再按 Work Item ID 隔离 |
| 精简后是否取消角色和任务隔离？ | ❌ 不取消；只减少公开步骤，隔离和职责检查仍由 Harness 自动执行 |

---

## 2. 协作模型

```
                    Work Item provider（协同真相源）
                    任务-A    任务-B    任务-C
                      │        │        │
         ┌────────────┼────────┼────────┼────────────┐
         ▼            ▼        ▼        ▼            ▼
       PM-甲        PM-乙    PM-丙     …            …
         │            │        │
         ▼            ▼        ▼
    harness-workspace/planning/tasks/A/   harness-workspace/planning/tasks/B/  harness-workspace/planning/tasks/C/   ← Git MR 入库
    phase0_pass   phase0_pass …
         │            │        │
         ▼            ▼        ▼
       Dev-1        Dev-2    Dev-3          ← 共享 Harness，各开分支
    agent_start(A) agent_start(B) …
         │            │        │
         └────────────┴────────┴──► monorepo 业务代码 MR
```

**三层隔离**：

| 层 | 隔离键 | 存储 |
|----|--------|------|
| 产品层 | `HARNESS_PRODUCT_ID` / `HARNESS_PRODUCT_ROOT` | 当前 shell session 或单次命令 |
| 协同层 | 当前产品 Work Item ID | Teambition / 飞书任务 / Jira |
| 规划层 | `harness-workspace/planning/tasks/<date>-<work-item-id>-<简称>/` | Git |
| 执行层 | `harness-workspace/runs/tasks/<work-item-id>/` | 本地（gitignore） |

多产品并行时不要在不同终端反复执行 `harness_init.sh use`。每个终端进入工作前固定一次产品上下文：

```bash
eval "$(bash .harness/scripts/harness_product.sh env --product-id product-a)"
bash .harness/scripts/agent_start.sh <work-item-id>
```

一次性命令使用：

```bash
bash .harness/scripts/harness_product.sh exec --product-id product-b -- bash .harness/scripts/work_item.sh list-mine
```

---

## 3. 角色分工

### 3.0 已有项目接入负责人 — Intake

首次把已有产品接入 Team Product R&D Harness 时，建议由 Tech Lead 运行扫描，PM/QA/架构负责人共同 review。

```bash
bash .harness/scripts/harness_knowledge.sh ensure
bash .harness/scripts/harness_intake.sh status
bash .harness/scripts/harness_intake.sh scan
```

协作规则：

- Tech Lead 负责确认服务边界、技术栈、运行/测试命令。
- PM 负责确认产品目标、领域术语、用户场景是否准确。
- QA 负责确认测试信号、已知风险和质量缺口。
- 只有 review 后的稳定事实才能进入 `knowledge/CONTEXT.md`、`knowledge/LESSONS.md` 或产品架构文档。
- 技术债必须转成当前产品 provider 的 Work Item，不能只留在 INTAKE 报告里。

### 3.1 产品经理（PM）— BMAD Planning

**并行规则**：每人自己的 feature 分支，不共改同一 `harness-workspace/planning/tasks/` 目录。

```bash
git checkout -b pm/kb-health-spec

PRODUCT_ROOT=/path/to/product
cp .harness/templates/product-spec.md "$PRODUCT_ROOT/harness-workspace/planning/product-specs/kb-health.md"
# 编辑验收标准…

bash .harness/scripts/work_item.sh draft-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/kb-health.md" --assignee <provider-user-id>
# 在 Codex 对话中确认标题、范围和负责人
bash .harness/scripts/work_item.sh sync-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/kb-health.md" --assignee <provider-user-id>
# → 当前产品 provider 创建任务，设置负责人，回写 #<work-item-id>

TASK_DIR="$PRODUCT_ROOT/harness-workspace/planning/tasks/$(date +%Y-%m-%d)-<work-item-id>-kb-health"
mkdir -p "$TASK_DIR"
cp tasks/_templates/00-任务卡.md tasks/_templates/03-实施方案.md tasks/_templates/04-实施记录.md "$TASK_DIR/"
# 编辑 00：Gate 1 已确认、任务编号=<work-item-id>、产品规格链接

bash .harness/scripts/planning_gate.sh L2 "$TASK_DIR"
git -C "$PRODUCT_ROOT" add harness-workspace/planning/product-specs harness-workspace/planning/tasks/
git commit -m "spec: kb-health BMAD Planning"
git push -u origin pm/kb-health-spec
# → 开 MR，合并后 Dev 可认领
```

**PM 注意**：

- `draft-spec` 只生成待确认草稿，不写 Teambition / 飞书 / Jira
- `sync-spec --assignee` 会按产品侧 `harness-workspace/project.yaml` 的 provider 建任务并设置负责人
- 所有 provider 的 L3 Product Spec 都必须声明 `work_item_type`；`story`/`task` 必须在 front matter 声明 `work_item_parent_id`，或向 `draft-spec` / `sync-spec` 传 `--parent-id`，`epic` 必须为顶层。Harness 会在写回 ID 前验证产品容器和父级落点；当前飞书支持精确远端校验，`noop` 仅提供本地 `guarded` 语义，无法提供 placement-aware `verify_binding` 的 provider 会 guarded block，不会静默降级
- `sync-spec` 使用 `bmad-work-item-v1`，只同步摘要、负责人、状态和 Harness Links；完整 BMAD 产物仍在 `harness-workspace/planning/`
- 不同产品线可选择不同 provider：Teambition 项目、飞书任务清单或 Jira project
- 验收标准须可测试、可勾选

### 3.2 开发工程师 — Harness Execution

**不需要**单独部署 Harness；**需要**认领 Work Item ID 并激活工作区。

```bash
git checkout main && git pull
git checkout -b feature/wi-<work-item-id>

# 推荐：一次生成 batch receipt，再启动任务（无需手拷或逐项 verify/pull）
harness --product-root "$PRODUCT_ROOT" plan --level L3 \
  --task-dir "$PRODUCT_ROOT/harness-workspace/planning/tasks/<task-dir>"
harness --product-root "$PRODUCT_ROOT" start <work-item-id>

# 查看当前激活任务
bash .harness/scripts/task_workspace.sh show-active

# 查看当前 provider 分给我的任务（Teambition/Jira 支持；飞书待租户搜索接口增强）
bash .harness/scripts/work_item.sh list-mine

# … Harness Execution：tasks-dag → TDD → 风险匹配验证 → check → MR …
```

`start` 会从 batch receipt 和 `03-实施方案.md` 写入边界生成 runtime scope；execution tier 按实际 diff/risk 选择，L3 不再自动等于 strict。再次启动同一 Work Item 不覆盖原 baseline，只允许增强 tier/scope，并拒绝 Work Item 换绑。

**开发注意**：

- Teambition 产品：`.env` 中 `DINGTALK_OPERATOR_USER_ID` 建议填 **本人** 钉钉 userId（审计与 list-mine）
- 飞书/Jira 产品：按 `.env.example` 填飞书应用或 Jira API token
- MR 必须包含 `harness-workspace/planning/tasks/<id>/planning_gate_pass.json` 及业务变更

### 3.3 Lead / QA

- **Lead**：不写业务代码；拆 `tasks-dag.md`，跑 `feedback_planner`
- **QA**：当前工序或 execution tier 要求独立验收时执行 `qa_sign_off`；凭证写入 `harness-workspace/runs/tasks/<work-item-id>/qa_approved_Tn.json`

---

## 4. 任务工作区（task_workspace）

### 4.1 目录结构

```
harness-workspace/runs/
├── active_task.json              # 兼容：旧流程的当前激活指针，新流程不写入
├── planning_gate_pass.json              # 兼容：当前激活的 phase0 副本
├── context.md                    # 兼容：当前 context 副本
└── tasks/
    └── <work-item-id>/
        ├── planning_gate_pass.json
        ├── context.md
        └── qa_approved_T1.json
```

Git 权威：`harness-workspace/planning/tasks/<date>-<work-item-id>-简称>/planning_gate_pass.json`（**须随 MR 提交**）。  
本地执行缓存：`harness-workspace/runs/tasks/<work-item-id>/`（Planning Gate、context、QA 凭证）。

### 4.2 命令

| 命令 | 作用 |
|------|------|
| `task_workspace.sh activate <work-item-id>` | 从 `harness-workspace/planning/tasks/` 恢复 Planning Gate 并激活 |
| `task_workspace.sh show-active` | 查看当前 Work Item ID |
| `task_workspace.sh infer-mr` | CI/MR：从 git diff 推断任务目录 |
| `task_workspace.sh qa-path <Tn>` | 查找 QA 凭证路径（排障） |
| `agent_start.sh <work-item-id>` | **内含 activate**，启动 Harness Execution |

### 4.3 切换任务

完成 MR 后做下一任务：

```bash
bash .harness/scripts/agent_start.sh <新work-item-id>
```

新流程不会切换共享 `active_task.json`；每个任务使用 `runs/tasks/<work-item-id>/`，因此多个任务不会互相覆盖。旧兼容命令仍可写 active pointer，**无需**删除旧目录。

---

## 5. 场景说明

### 场景 A：两 PM 同时写不同规格 — ✅

- PM-甲：`harness-workspace/planning/product-specs/feature-a.md` + `harness-workspace/planning/tasks/...-taskA/`
- PM-乙：`harness-workspace/planning/product-specs/feature-b.md` + `harness-workspace/planning/tasks/...-taskB/`
- 各开分支 MR，Git 冲突概率低

### 场景 B：两开发同时做不同任务 — ✅

- Dev-1：`feature/wi-taskA`，改 `services/catalog_service/...`（03 已登记路径）
- Dev-2：`feature/wi-taskB`，改 `services/gateway/...`
- **路径不重叠**则 merge 顺利；重叠由 Lead 拆任务时避免

### 场景 C：同一人开两个 Cursor 窗口做两任务 — ⚠️ 不建议

新流程按 Work Item ID 隔离，多个任务可并行；QA 凭证、planning credential 和 context 都写入各自 task scope。只有调用旧兼容命令时才受单一 active pointer 限制。

### 场景 D：多 MR 并行 — ✅（须遵守）

- 每个 MR **只变更一个** `harness-workspace/planning/tasks/<id>/` 目录（或使用 `HARNESS_TASK_DIR`）
- CI 已 **移除**「取最新 tasks 目录」的危险兜底

### 场景 E：已有项目首次接入 — ✅（须 review）

- 先运行 `harness_intake.sh scan` 生成 `harness-workspace/evidence/intake-reports/<date>-INTAKE.md`
- 再由 Tech Lead / PM / QA 按职责 review
- 最后运行 `harness_intake.sh apply-review`，把已确认事实自动写入 `CONTEXT.md`、`LESSONS.md`、`REFERENCE_SYSTEMS.md`；架构文档或技术债 Work Item 仍由负责人确认后单独落地
- 未 review 的 INTAKE 报告不得作为 Agent 实现依据

---

## 6. 环境配置（多人）

| 配置 | 位置 | 说明 |
|------|------|------|
| `work_item.provider` | 产品侧 `harness-workspace/project.yaml` | 长期 provider 选择 |
| `providers.teambition.project_id` | 产品侧 `project.yaml` | 同一钉钉项目，非密钥 |
| `providers.feishu.tasklist_guid` | 产品侧 `project.yaml` | 飞书任务清单/容器，非密钥 |
| `providers.feishu.list_tasks_path/list_query` | 产品侧 `project.yaml` | 飞书 `list-mine` 使用的任务列表或搜索参数 |
| `providers.feishu.assignee_id` | 产品侧 `project.yaml` | 可选团队默认负责人，非密钥 |
| `providers.jira.base_url/project_key` | 产品侧 `project.yaml` | Jira 站点与 project，非密钥 |
| `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET` | 本机 `.env` / Secret | Teambition 应用密钥 |
| `DINGTALK_OPERATOR_USER_ID` | 本机 `.env` | 每人自己的钉钉 userId |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 本机 `.env` / Secret | 飞书企业自建应用 |
| `FEISHU_TASKLIST_GUID` | 本机 `.env` | 兼容默认；仅产品未配置任务清单时生效 |
| `FEISHU_TASKLIST_GUID_OVERRIDE` | 单次环境变量 | 显式临时覆盖产品任务清单；不得作为多产品共享默认 |
| `FEISHU_ASSIGNEE_ID` | 本机 `.env` | 覆盖产品默认负责人，按租户 ID 类型 |
| `TEAMBITION_ASSIGNEE_ID` | 本机 `.env` | 覆盖产品默认负责人 |
| `JIRA_ASSIGNEE_ID` | 本机 `.env` | 覆盖产品默认 Jira accountId |
| `JIRA_EMAIL` / `JIRA_API_TOKEN` | 本机 `.env` / Secret | Jira API 访问 |
| `WORK_ITEM_PROVIDER` | 本机环境变量 | 临时覆盖，长期不用 |

> `noop` 下 `list-mine` 恒为空；`draft-spec` 只生成草稿，`sync-spec` 会生成语义化本地 ID，但不会调用外部协同系统。

`.env` 在每人本地，**不提交 Git**（见 `.env.example`）。

---

## 7. CI / MR 约定

### 7.1 MR 必须包含

- `harness-workspace/planning/tasks/<id>/planning_gate_pass.json`
- 对应 `harness-workspace/planning/tasks/<id>/` 内文档变更（若改方案）
- 业务代码变更

### 7.2 CI 推断任务目录

1. 若 MR diff **仅涉及一个** `harness-workspace/planning/tasks/<id>/` → 自动用该目录 phase0  
2. 否则设置 CI 变量 `HARNESS_TASK_DIR=harness-workspace/planning/tasks/...`（相对产品根）

### 7.3 不再支持

- ❌ CI 自动选「最新 tasks 目录」（多任务并行时会绑错）

---

## 8. 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| `PLANNING_GATE_NOT_FOUND` | 未 MR 合并 harness-workspace/planning/tasks/ 或未 pull | `git pull` 后 `agent_start <work-item-id>` |
| `WORK_ITEM_MISMATCH` | 参数 ID ≠ 00-任务卡 | 核对当前 provider 的 Work Item ID |
| 两人改同一文件冲突 | 正常 Git | Lead 拆任务避免路径重叠 |
| `list-mine` 为空 | 任务未指派给当前账号，或 provider 配置不完整 | 检查 Teambition/Jira 指派；飞书检查 `tasklist_guid` / `list_query` 与 `FEISHU_ASSIGNEE_ID` |
| MR CI phase0 失败 | MR 改了多个 harness-workspace/planning/tasks 目录 | 一 MR 一任务，或设 `HARNESS_TASK_DIR` |

---

## 9. 与全景手册的关系

| 文档 | 用途 |
|------|------|
| [Harness_全景手册.md](./Harness_全景手册.md) | 设计、方案、评估、演进 |
| **本文** | 多人 PM/Dev 协作场景与命令 |
| [USAGE.md](./USAGE.md) | 三项主动作 + 只读 status 的使用模型与过渡期兼容参考 |
| [BMAD_Prelude.md](./BMAD_Prelude.md) | BMAD Planning 专章 |

---

*维护：协作机制变更时同步更新本文与全景手册 §19。*
