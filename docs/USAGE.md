# Harness 使用方式（公开入口与兼容参考）

> **全景设计文档**：[Harness_全景手册.md](./Harness_全景手册.md) — 思想、方案、评估、演进  
> **通用化说明**：[Team_Product_Harness.md](./Team_Product_Harness.md) — 任意产品研发团队的 Harness 模型  
> **已有项目接入**：[Brownfield_Intake.md](./Brownfield_Intake.md) — 已有项目接入的定位、报告和 review 规则  
> **多人协作**：[COLLABORATION.md](./COLLABORATION.md) — 多 PM/Dev 并行场景与命令  
> **精简强制执行目标**：[design-docs/lean-enforcement.md](./design-docs/lean-enforcement.md) — 一个入口面、一个权威结果、风险分层与成本约束  
> 本文档是 **Team Product R&D Harness 的 canonical 使用说明**。不可跳过的是“先形成与风险匹配的计划，再实施并验证”的合规语义，不是让人或 Agent 手工执行固定数量的命令。目标正常路径只暴露 `start / status / finish`；BMAD Planning、TDD、QA、安全、知识和协同能力由统一入口按 planning level 与 execution tier 编排。

`AGENTS.md` 为最小地图，本文件说明公开使用方式，并保留目标入口落地前仍需使用的兼容命令参考。

**实现状态说明**：`harness start/status/finish`、`harness workspace audit`、`harness migrate-task` 与 `runs/tasks/<id>/result.json` 已进入可执行阶段，入口为 `.harness/scripts/harness`。当前任务执行能力可用于 `local` 接入；`guarded` guards 与结构化 `assurance` 字段仍待实现，平台 required、发布依赖和 provider 生命周期未全部验证，所以兼容字段继续显示 `shadow`，不得标记 `enforced`。旧命令暂保留用于兼容和诊断。

**升级兼容说明**：已有产品不做全量 workspace 迁移。planning、knowledge 和历史 evidence 原位保留；新任务写新结果；只有进行中或重开的任务补最小运行状态。详细矩阵见 [精简执行设计 §10](./design-docs/lean-enforcement.md#10-历史数据与兼容升级)。

## 阅读与执行模型

本手册同时记录目标入口和过渡期实态，但二者不能混用：

| 场景 | 使用方式 |
|------|----------|
| 目标正常路径（尚未实现） | `harness start [task-id]` → `harness status [task-id]` → `harness finish [task-id]` |
| 当前过渡期 | 按任务角色读取对应章节；不要把第 2–8 节串成每个任务都要人工执行的总清单 |
| runtime 开发/排障 | 才直接调用 `.harness/scripts/` 中的内部命令 |

统一入口落地后，以下能力继续存在，但不再增加使用者步骤：

| 保留能力 | 来源 | 目标触发方式 | 当前说明 |
|----------|------|--------------|----------|
| 产品分析、规格、方案与实现就绪 | BMAD Method | `start` 按 planning level / execution tier 选择 Quick Flow 或完整规划 | 第 2 节、[BMAD_Prelude.md](./BMAD_Prelude.md) |
| 短地图、渐进上下文、机械反馈与文档园艺 | OpenAI Harness | `start` 加载最小上下文，`finish` 统一检查 | `AGENTS.md`、第 8 节 |
| L1/L2/L3 规划分级和任务模板 | planning level | 保留为 planning level 与 legacy 兼容，不决定最终 execution tier | 第 2.3 节、`.harness/workflows/` |
| TDD、调试纪律和完成前验证 | Superpowers | 按 tier 内部执行 | 第 4–5 节 |
| 真实环境调查和浏览器 QA | GStack | 前端、交互或高风险变更按需触发 | 第 5.4 节 |
| CONTEXT/LESSONS 和任务证据语义 | Flow-X | 上下文按需检索；证据按恢复、tier 或长期候选生成 | 第 2、5–7 节 |
| Agent 审 Agent 与失败重试 | Ralph / Agent Review | `standard/strict` 由 `finish` 编排 | 第 4、6 节 |
| 已有项目事实建档 | Brownfield Intake | 首次接入或事实漂移时触发，不按任务重复 | 第 2.0.1 节 |
| 多 provider 协同 | Work Item adapter | `start/finish` 绑定并同步生命周期 | 第 2.1、2.5 节 |

目标 `status` 和 `result.json` 必须统一呈现输入/输出 Token、上下文字符数、Agent 调用数、gate 耗时、重跑次数、确定性 gate 缓存命中数及遥测完整性；这些数据不再拆成单独成本报告。完整能力保留规则见 [精简强制执行设计 §1.1](./design-docs/lean-enforcement.md#11-能力保留契约)。

---

## 0. 当前兼容：前置条件

| 项 | 要求 |
|----|------|
| 产品根 | 任意产品仓库，例如 `product-repo/`（含业务代码、架构文档、`harness-workspace/`） |
| harness-engineering 工作目录 | 任意位置的 `harness-engineering/`（含 `AGENTS.md`、`.harness/`） |
| Product context | 并行研发用 `HARNESS_PRODUCT_ID` / `HARNESS_PRODUCT_ROOT` 或 `harness_product.sh`；`harness_init.sh use` 只设置默认兜底 |
| BMAD Planning 产出 | 产品侧 `harness-workspace/planning/`，不是 harness-engineering 目录 |
| BMAD Method | 推荐由 `harness_init.sh init --install-bmad` 在产品根非交互安装 → `_bmad/` |
| Shell | bash 3.2+ |
| Python | 3.9+（`workspace_paths.py`、`work_item.py` 等） |
| 权限 | `chmod +x .harness/scripts/*.sh`（首次克隆后执行一次） |

**禁止**：绕过 `.harness/scripts/` 直接在宿主终端跑构建/测试。默认走 controlled argv 后端；需要容器隔离时显式切 Docker backend。  
**废弃**：`tools/` 仅转发，勿在新流程中引用。

---

## 1. 当前兼容：反馈协议

每个钩子脚本 **始终以 exit 0 结束**，结果写在 stdout 的 JSON 中。人类在终端直跑时默认 pretty：

```json
{
  "decision": "pass",
  "reason": "..."
}
```

脚本捕获、CI、管道时保持单行 raw JSON：

```json
{"decision":"pass","reason":"..."}
```

**收到 `block` 时**：读取 `reason` → 修改计划/代码/文档 → 重新调用同一脚本。  
**禁止**：忽略 JSON、用 shell 退出码判断、向人类辩解以绕过门禁。

解析示例：

```bash
RESULT=$(bash .harness/scripts/validate_harness.sh)
echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['decision'])"
```

需要强制 raw 输出时设置 `HARNESS_PRETTY=0`。`pretty.sh` 仍可用于格式化日志、文件或外部命令输出：

```bash
HARNESS_PRETTY=0 bash .harness/scripts/harness_init.sh list

bash .harness/scripts/harness_init.sh list > /tmp/result.json
bash .harness/scripts/pretty.sh < /tmp/result.json
```

---

## 2. 当前兼容：BMAD Planning

> **过渡期规则**：当前 `agent_start.sh` 之前必须有经 [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD) 形成的 BMAD Planning 结果和 Planning Gate。L1 使用 Quick Flow，L2/L3 使用相应完整度；不要求低风险任务运行完整 Analysis / Planning / Solutioning 仪式。产出写入产品侧 **`harness-workspace/planning/`**（由 `harness-workspace/project.yaml` 配置，见 [BMAD_Prelude §0.1](./BMAD_Prelude.md#01-bmad-planning-目录配置产品-harness-workspaceprojectyaml)）。目标状态由 `start` 选择深度并机械阻止遗漏。
> **BMAD 在产品根执行**；**Harness 脚本在 `harness-engineering/` 执行**。详见 [BMAD_Prelude §0.2](./BMAD_Prelude.md#02-bmad-method-在产品根执行必遵)。

### 2.0 初始化与路径

```bash
# harness-engineering — 首次绑定产品并创建产品 workspace
cd /path/to/harness-engineering
bash .harness/scripts/harness_init.sh init \
  --product-root /path/to/product \
  --product-id my-product \
  --product-name "My Product" \
  --profile generic \
  --install-bmad
```

`--install-bmad` 会在产品根非交互执行 `npx bmad-method install --directory <product-root>`，默认模块为 `bmm,tea`，默认工具为 `codex,cursor`，并将 BMAD 原生输出配置到 `harness-workspace/bmad-output/`。初始化会创建 `planning-artifacts/`、`design-artifacts/`、`implementation-artifacts/`、`test-artifacts/`，其中 `design-artifacts/` 同时配置给 BMM/CIS 的 design 输出 key。安装结束后若产品根没有生成 `_bmad/`，init 必须返回 `block`。

可按团队需要用环境变量覆盖安装参数：

| 变量 | 默认值 |
|------|--------|
| `HARNESS_BMAD_MODULES` | `bmm,tea` |
| `HARNESS_BMAD_TOOLS` | `codex,cursor` |
| `HARNESS_BMAD_COMMUNICATION_LANGUAGE` | `Chinese` |
| `HARNESS_BMAD_DOCUMENT_LANGUAGE` | `Chinese` |
| `HARNESS_BMAD_USER_NAME` | 产品名称 |

```bash
# 后续设置默认产品（单产品或兜底）
bash .harness/scripts/harness_init.sh use --product-id my-product
bash .harness/scripts/harness_init.sh list

# 多产品并行：给当前终端固定产品上下文，不修改 active-product.json
eval "$(bash .harness/scripts/harness_product.sh env --product-id my-product)"

# 单次命令：只让这一条命令使用指定产品
bash .harness/scripts/harness_product.sh exec --product-id my-product -- bash .harness/scripts/check.sh

# 如未使用 --install-bmad，可在产品根手动补装 BMAD，再回到 harness 重新 init/use。
cd /path/to/product
npx bmad-method install

# harness-engineering — 查看产品 workspace 解析路径
cd /path/to/harness-engineering
python3 .harness/scripts/workspace_paths.py json
python3 .harness/scripts/workspace_paths.py ensure-dirs

# 初始化项目级知识沉淀目录
bash .harness/scripts/harness_knowledge.sh ensure

# 全新项目：BMAD Planning 完成后，把产品目标/蓝图/架构/任务边界同步到 CONTEXT
bash .harness/scripts/harness_knowledge.sh sync-planning

# 已有项目接入：扫描既有代码、关键入口、文档、测试与技术栈
bash .harness/scripts/harness_intake.sh status
bash .harness/scripts/harness_intake.sh scan
```

配置分工：harness-engineering `.harness/products/registry.yaml` 管本机产品台账；产品侧 `harness-workspace/project.yaml` 管 workspace 结构。说明见 [`Harness_Product_Workspace.md`](./Harness_Product_Workspace.md) 与产品侧 `harness-workspace/planning/README.md`。

产品根解析优先级：命令行 `--product-root/--product-id` → 环境变量 `HARNESS_PRODUCT_ROOT/HARNESS_PRODUCT_ID` → 当前目录发现产品 workspace → `active-product.json` → 旧兼容标记。显式指定产品但找不到时会直接 `block`，不会静默回退到默认 active product。

Work Item provider 也属于产品侧配置。长期选择写在 `<product-root>/harness-workspace/project.yaml`，本机 `.env` 只放密钥；`WORK_ITEM_PROVIDER` 仅用于临时覆盖。

BMAD Planning 到 Work Item 的同步遵循 `bmad-work-item-v1`：先用 `draft-spec` 在 Codex 对话中生成待确认任务草稿，确认标题、范围和负责人后，再用 `sync-spec --assignee <provider-user-id>` 创建外部任务。外部任务只保存摘要、负责人、状态、讨论和 Harness Links；完整产品规格、执行计划和任务包以 `harness-workspace/planning/` 为真相源。详见 [BMAD_Work_Item_Contract.md](./BMAD_Work_Item_Contract.md)。

| 产品场景 | 产品侧 `project.yaml` | 本机 `.env` / 环境变量 |
|----------|----------------------|-------------------------|
| 钉钉 Teambition 项目 | `provider: teambition`、`providers.teambition.project_id`、可选 `providers.teambition.assignee_id` | `DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_OPERATOR_USER_ID`；个人覆盖用 `TEAMBITION_ASSIGNEE_ID` |
| 飞书任务 | `provider: feishu`、`providers.feishu.tasklist_guid/list_tasks_path/list_query`、可选 `providers.feishu.assignee_id` | `FEISHU_APP_ID`、`FEISHU_APP_SECRET`；个人覆盖用 `FEISHU_ASSIGNEE_ID` |
| Jira | `provider: jira`、`providers.jira.base_url`、`providers.jira.project_key`、`issue_type`、可选 `providers.jira.assignee_id` | `JIRA_EMAIL`、`JIRA_API_TOKEN`；个人覆盖用 `JIRA_ASSIGNEE_ID` |
| 本地/离线 | `noop` | 无 |

业务质量命令也属于产品侧配置。严格 CI 下，`quality.commands.lint/test` 缺失会 `block`：

```yaml
quality:
  env:
    PYTHONPATH: services
  required:
    lint: true
    test: true
  commands:
    lint:
      - name: python syntax check
        builtin: python-syntax
        paths: [services, tests]
    test:
      - name: service scaffold unittest
        cmd: [python3, -m, unittest, discover, -s, tests]
```

知识沉淀目录：

- `harness-workspace/knowledge/CONTEXT.md` — 项目共享上下文
- `harness-workspace/knowledge/LESSONS.md` — 跨任务失败教训
- `harness-workspace/knowledge/REFERENCE_SYSTEMS.md` — 上游与重度依赖系统的核心逻辑、扩展点、代码入口和禁改边界
- `harness-workspace/evidence/progress/` — 中断恢复
- `harness-workspace/evidence/summaries/` — 任务摘要
- `harness-workspace/evidence/test-reports/` — 测试证据
- `harness-workspace/evidence/review-reports/` — 审查证据
- `harness-workspace/evidence/growth-reports/` — 自我成长候选报告
- `harness-workspace/evidence/intake-reports/` — 已有项目接入候选报告

排障过程中如果发现跨任务可复现的教训，不要等人提醒再手写 `LESSONS.md`。Agent 应先把它记录成可 review 的成长候选证据：

```bash
bash .harness/scripts/harness_growth.sh capture \
  --title "Feishu tasklist auth" \
  --summary "经验沉淀：飞书任务清单 1470403 不是普通 API scope 问题，必须把应用作为 app/editor 清单成员初始化。" \
  --category "lesson" \
  --trigger "配置 providers.feishu.tasklist_guid 后 diagnose 失败" \
  --failed "只开通飞书 API scope 或把机器人群组加入清单" \
  --cause "tenant_access_token 仍按应用身份做资源级鉴权" \
  --next-action "用清单 owner/editor 的 user_access_token 跑 feishu-tasklist-member"
```

`capture` 只写 `harness-workspace/evidence/progress/*-GROWTH-CAPTURE.md`，随后由 `harness_growth.sh scan` 生成 GROWTH 报告，人工 review 后再用 `harness_growth.sh apply-review` 进入 `CONTEXT.md` / `LESSONS.md`。它适合外部系统权限坑、重复失败路径、架构边界、新默认行为和需要以后自动复用的排障结论；不要把 token、secret 或完整请求头写入摘要。

全新项目的首批 `CONTEXT.md` 不来自 intake，而来自 BMAD Planning：产品规格、产品蓝图、架构/执行计划、任务卡和 BMAD 原生 planning artifacts。`planning_gate.sh` 通过后会自动执行 `harness_knowledge.sh sync-planning`；也可以手动重跑这个命令刷新受管区块。

已有项目接入报告只读取产品根下的既有代码和文档，并排除 `harness-workspace/`、`harness-workspace*` 备份、`_bmad/`、`bmad-output/`、`.env`、依赖缓存与本地 Agent 缓存。报告中的候选不会绕过人工判断直接进入长期知识；人工或 Agent review 后运行 `apply-review`，由 Harness 自动写入 `CONTEXT.md` / `LESSONS.md` / `REFERENCE_SYSTEMS.md` 的受管区块。报告顶部的 `## 0. Review 工作台（先处理）` 是 intake 完成度看板，处理完后把 `- [ ]` 改成 `- [x]`。

如果仓库中包含上游官方代码或 vendor 代码，先在产品侧 `project.yaml` 配置 `intake.scopes`。例如把 `OpenViking/`、`deer-flow/` 设为 `upstream-reference + debt: ignore`，再把 `deer-flow/mobile/` 设为 `product-owned + debt: track`，即可让报告只沉淀上游核心逻辑/特性，同时只追踪自研范围的技术债。

完成报告顶部 review 后，不需要手工编辑 knowledge 文件，直接让 Harness 应用：

```bash
# 已全部勾选时正式沉淀
bash .harness/scripts/harness_intake.sh apply-review

# 未全部勾选但想先生成草稿时
bash .harness/scripts/harness_intake.sh apply-review --allow-pending
```

### 2.0.1 已有项目接入 Review

首次接入已有产品时，在创建新功能规格前先 review INTAKE 报告：

| 报告内容 | Review 结果 |
|----------|-------------|
| 产品目标、核心用户、领域术语 | `apply-review` 写入 `knowledge/CONTEXT.md` |
| 服务目录、技术栈、运行/测试命令 | `apply-review` 写入 `knowledge/CONTEXT.md`，必要时再补产品 README |
| DeerFlow、OpenViking 等重度依赖 | 报告提名关键文档和关键代码入口；`apply-review` 写入 `knowledge/REFERENCE_SYSTEMS.md` 的证据入口；Agent/架构负责人深读后补核心机制摘要 |
| 重复失败、临时方案、返工线索 | `apply-review` 写入 `knowledge/LESSONS.md`，需要排期时创建技术债 Work Item |
| 服务边界、数据流、部署边界 | 迁入产品架构文档 |
| 一次性噪音或证据不足项 | 留在 INTAKE 报告，不升级 |

完整规则见 [Brownfield_Intake.md](./Brownfield_Intake.md)。

### 2.1 产品规格（Planning → `harness-workspace/planning/product-specs/`）

```bash
cd /path/to/harness-engineering
PRODUCT_ROOT=/path/to/product
cp .harness/templates/product-spec.md "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md"
# 编辑 front matter 与验收标准
bash .harness/scripts/work_item.sh draft-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" --assignee <provider-user-id>
# 在 Codex 对话中确认任务标题、范围和负责人
bash .harness/scripts/work_item.sh sync-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" --assignee <provider-user-id>
# Planning Gate/Execution 状态变化后，用受审计文件同步既有任务描述（当前支持飞书）
bash .harness/scripts/work_item.sh update-description --id <work-item-id> --file <description.md>
```

`draft-spec` 不调用外部 API、不会改 Product Spec；`sync-spec` 会为未绑定的验收勾选项创建当前 provider 的 Work Item，在任务描述里写入 Product Spec、Exec Plan、Task Package、Context 的链接和负责人，并把 `#<work-item-id>` 回写到勾选行尾。`update-description` 先校验既有任务，再用完整 Markdown 文件更新描述；它不创建新任务，也不改变完成状态。

### 2.2 执行计划（Solutioning → `harness-workspace/planning/exec-plans/active/`）

```bash
cp .harness/templates/exec-plan.md "$PRODUCT_ROOT/harness-workspace/planning/exec-plans/active/某功能.md"
# linked_spec 填 product-specs/某功能.md
```

### 2.3 任务分级与建档（→ `harness-workspace/planning/tasks/`）

| 级别 | 任务目录 | Work Item | 模板（在 harness-engineering） |
|------|----------|-----------|-------------------------------|
| L1 | 当前可不建 | 外部 provider 豁免 | 目标状态由 Harness 生成本地任务 ID，仍保持可追踪 |
| L2 | **必须** | 当前产品 provider 的 Work Item ID | `tasks/_templates/00` + `03` + `04` |
| L3 | **必须** | 当前产品 provider 的 Work Item ID | `00`～`06` 全套 |

```bash
TASK_DIR="$PRODUCT_ROOT/harness-workspace/planning/tasks/$(date +%Y-%m-%d)-<work-item-id>-功能简称"
mkdir -p "$TASK_DIR"
cp tasks/_templates/00-任务卡.md tasks/_templates/03-实施方案.md tasks/_templates/04-实施记录.md "$TASK_DIR/"
```

`00-任务卡.md` 链接格式：`product-specs/...`、`exec-plans/active/...`；**Gate 1 → 已确认**。

### 2.4 前导门禁

```bash
bash .harness/scripts/planning_gate.sh L2 "$TASK_DIR"
```

凭证：`harness-workspace/runs/planning_gate_pass.json` + `harness-workspace/planning/tasks/.../planning_gate_pass.json`。`phase0_pass.json` 会作为历史兼容副本同步写入。

L2/L3 的 `03-实施方案.md` 必须通过 7 字段任务契约：

```bash
bash .harness/scripts/task_contract_check.sh --task-dir "$TASK_DIR"
```

### 2.5 agent_start

```bash
bash .harness/scripts/agent_start.sh <work-item-id>
```

从 `harness-workspace/planning/tasks/` 自动恢复 Planning Gate，并把产品侧 `CONTEXT.md`、`LESSONS.md`、`REFERENCE_SYSTEMS.md` 摘要注入本次任务上下文；工作区在 `harness-workspace/runs/tasks/<id>/`。

---

### 2.6 多人协作要点

> 完整场景与命令见 **[COLLABORATION.md](./COLLABORATION.md)**。

| 角色 | 并行方式 | 关键命令 |
|------|----------|----------|
| PM | 各开分支，独立 `harness-workspace/planning/tasks/<id>/` | `work_item.sh draft-spec` → 确认 → `work_item.sh sync-spec` → `planning_gate` |
| Dev | 共享 Harness，一任务一分支 | `agent_start <work-item-id>`、`work_item.sh list-mine` |
| 切换任务 | 一人一时一个 Work Item ID | `agent_start <新work-item-id>` |

`.env` 按当前产品 provider 填写：Teambition 用 `DINGTALK_OPERATOR_USER_ID`，飞书用 `FEISHU_*`，Jira 用 `JIRA_*`。

---

## 3. 当前兼容：人类工程师进入执行闭环

### 3.1 写清验收标准

将 PRD/用户故事放入 `$PRODUCT_ROOT/harness-workspace/planning/product-specs/<功能名>.md`，先生成待确认任务草稿，再同步到当前产品 Work Item provider：

```bash
PRODUCT_ROOT=/path/to/product
bash .harness/scripts/work_item.sh draft-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" --assignee <provider-user-id>
bash .harness/scripts/work_item.sh sync-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" --assignee <provider-user-id>
```

### 3.2 初始化 Agent 工作区（自动恢复 Planning Gate）

```bash
bash .harness/scripts/agent_start.sh <work-item-id>             # 须与 00-任务卡一致
bash .harness/scripts/task_workspace.sh show-active              # 确认当前激活任务
```

成功时返回 `{"decision":"pass","reason":"AGENT_READY: ..."}`，并生成：

- `harness-workspace/runs/tasks/<work-item-id>/planning_gate_pass.json` — 任务隔离工作区（兼容同步 `phase0_pass.json`）
- `harness-workspace/runs/tasks/<work-item-id>/context.md` — 本任务上下文，包含 BMAD Planning、协同系统摘要和产品知识注入
- `harness-workspace/runs/tasks/<work-item-id>/worktree_baseline.json` — 首次启动时的 tracked/untracked 指纹；恢复任务不会覆盖
- `harness-workspace/runs/context.md` — 任务上下文（兼容副本）

### 3.3 复杂需求：执行计划

```bash
PRODUCT_ROOT=/path/to/product
cp .harness/templates/exec-plan.md "$PRODUCT_ROOT/harness-workspace/planning/exec-plans/active/my-feature.md"
# 编辑目标、验收标准、进度日志后，再让 Lead 拆 tasks-dag.md
```

### 3.4 交给 Lead Agent

在 Cursor/Claude 中载入本仓库，以 **lead-agent** 身份阅读 `CLAUDE.md`，下达需求（可附带当前产品 Work Item ID）。

### 3.5 合并前人类 Review

Agent 完成 `mr_ready.sh` 且 CI 通过后，人类做**业务语义** Review（非替代 QA 机械门禁）。

---

## 4. 当前兼容：Lead Agent

身份：`lead-agent`（见 `CLAUDE.md`）。**严禁修改业务代码文件。**

### 4.1 拆任务 DAG

```bash
cp .harness/templates/tasks-dag.md "$TASK_DIR/tasks-dag.md"
# 按需求编辑 T1、T2… 负责人与依赖
```

格式示例：

```markdown
- [ ] T1: (user-service) 新增登录 API / 负责人: backend-agent / 前置依赖: 无
- [ ] T2: (web) 登录页对接 / 负责人: frontend-agent / 前置依赖: T1
- [ ] T3: QA 验收 T1-T2 / 负责人: qa-evaluator / 前置依赖: T2
- [ ] T4: GC 清扫 / 负责人: gc-sweeper / 前置依赖: T3
```

### 4.2 计划门禁（调度子代理前）

```bash
bash .harness/scripts/feedback_planner.sh '[ ] T1 services/catalog_service/app/api/health.py 写失败测试 [ ] 写最小实现 [ ] 沙箱跑测试'
```

须同时包含 **测试/TDD** 与 **`[ ]` 勾选语法**，否则 `block`。

### 4.2.1 任务契约与 DAG 同步门禁

调度子代理前，Lead 必须确认 `03-实施方案.md` 与 `tasks-dag.md` 一致：

```bash
bash .harness/scripts/task_contract_check.sh --task-dir "$TASK_DIR"
bash .harness/scripts/dag_sync_check.sh --task-dir "$TASK_DIR"
```

规则：

- `03-实施方案.md` 的实现任务使用 7 字段契约：`id/read_files/write_files/action/verify/done`
- `tasks-dag.md` 的实现任务 ID 必须覆盖 03；QA/GC 可使用 `T-QA`、`T-GC`
- `write_files` 必须落在结构白名单内
- 缺少任务目录内 `tasks-dag.md` 或 active `task_dir` 默认 `block`；诊断场景才可显式加 `--allow-skip`

### 4.3 调度子代理

按 DAG 顺序唤起 `backend-agent` / `frontend-agent`，明确告知：

- 任务 ID（如 T1）
- 须使用的沙箱命令格式
- 完成后不得自签被要求的独立验收；须回报 Lead，由 Lead 按当前工序和风险决定是否触发 QA

### 4.4 当前需要独立 QA 的任务

当当前 L2/L3 兼容流程或目标 execution tier 要求独立 QA 时，不得由实现者直接勾选 `[x]`。流程：

```bash
# 1. 唤起 qa-evaluator 审查 T1
# 2. QA 执行签章（见第 5 节）
bash .harness/scripts/qa_sign_off.sh T1 pass '边界、安全、契约测试已通过'
# 3. 物理门禁
bash .harness/scripts/subagent-pr-gate.sh T1
# 4. 证据门禁：QA 签章 + TEST/REVIEW 报告
bash .harness/scripts/qa_evidence_check.sh
# 5. 三方均 pass 后，方可在 tasks-dag.md 将 T1 改为 [x]
```

### 4.5 Epic 收尾：按需 GC + 合并

仅当 execution tier、跨模块重构或明显熵减风险要求独立 GC 时运行 `memory-sweep`；否则直接进入最终检查，不创建空 GC 任务。

```bash
bash .harness/scripts/memory-sweep.sh T-GC   # 仅在独立 GC 被要求时
bash .harness/scripts/check.sh
bash .harness/scripts/mr_ready.sh --message "feat: 完成某功能"
```

---

## 5. 当前兼容：Backend / Frontend Agent

身份：`backend-agent` 或 `frontend-agent`。可读 `.harness/rules/code-style-*.md`。

### 5.1 领取任务

从 Lead 获取 `tasks-dag.md` 中属于自己的条目（如 T1），确认前置依赖已为 `[x]`。

### 5.2 TDD 循环

```bash
# 示例：Node 项目
bash .harness/scripts/run_in_sandbox.sh 'npm test -- --testPathPattern=login'

# 示例：Go 项目
bash .harness/scripts/run_in_sandbox.sh 'go test ./internal/auth/...'

# 示例：Java 项目
bash .harness/scripts/run_in_sandbox.sh 'mvn -q test -Dtest=LoginServiceTest'
```

失败时脚本返回 `block` 与截断日志 → 自我修复后重跑，直至 `pass`。

默认 `run_in_sandbox.sh` 使用 controlled 后端：不经 shell eval，拦截 shell 控制符、提权和危险删除。需要容器后端时使用：

```bash
HARNESS_SANDBOX_BACKEND=docker \
HARNESS_SANDBOX_IMAGE=python:3.11-slim \
bash .harness/scripts/run_in_sandbox.sh 'python3 -m unittest discover -s tests'
```

Docker 后端默认 `HARNESS_SANDBOX_DOCKER_NETWORK=none`，并把当前解析出的 product 根挂载到容器 `/workspace`。远程执行器使用同一入口：设置 `HARNESS_SANDBOX_BACKEND=remote`、HTTPS `HARNESS_SANDBOX_REMOTE_URL`、`HARNESS_SANDBOX_WORKSPACE_REF`、`HARNESS_SANDBOX_REMOTE_TOKEN` 和可选 `HARNESS_SANDBOX_SUBJECT_DIGEST`。客户端只发送 JSON argv 与 workspace ref，不发送 shell 字符串或本机目录；返回 subject 不匹配时 fail closed。remote 协议已接入，但 Firecracker/远程 executor 实机隔离仍需部署验证。

### 5.3 调试纪律

```bash
bash .harness/scripts/gstack_investigate.sh \
  '假说1:  mock 未注入导致 NPE' \
  '假说2:  时区导致 token 过期断言失败'
```

未登记假说不得继续盲目改代码。

### 5.4 前端额外：浏览器 QA

首次使用前先检查环境：

```bash
bash .harness/scripts/browser_qa_setup.sh check
```

未安装 Playwright Chromium 时，显式安装：

```bash
bash .harness/scripts/browser_qa_setup.sh install --install-package
```

应用启动后：

```bash
python3 .harness/scripts/browser_qa.py http://localhost:3000 --action audit
python3 .harness/scripts/browser_qa.py http://localhost:3000 --action screenshot
python3 .harness/scripts/browser_qa.py http://localhost:3000 --action click-test --selector '[data-testid=save]'
python3 .harness/scripts/browser_qa.py http://localhost:3000 --action scenario --scenario tests/browser/login.json
```

`browser_qa.py` 只接受真实 Playwright 证据。默认报告、截图与 trace 写入产品侧 `harness-workspace/runs/browser-qa/`；未安装 Playwright、页面无法访问、console error 或 network failure 都会返回 `block`。`scenario` JSON 使用 `steps` 数组，最多 50 步，只允许 `click`、`fill`、`press`、`expect-visible`、`expect-text`、`wait-for-url`，并在报告中记录逐步 decision；默认禁止读取产品根之外的场景。`click-test --script` 仅作兼容，不作为新场景首选。

### 5.5 完成汇报

向 Lead 汇报变更文件列表、沙箱测试 `pass` 输出和已知风险。只有 execution tier、任务恢复或人工阅读需要时才生成对应文件：

- `harness-workspace/evidence/summaries/<taskId>-<Tn>-SUMMARY.md`
- 如中断：`harness-workspace/evidence/progress/<taskId>-<Tn>-PROGRESS.md`

**禁止**自行执行 `qa_sign_off.sh` 或 `subagent-pr-gate.sh`（QA 专属）。

---

## 6. 当前兼容：QA Evaluator

身份：`qa-evaluator`（见 `.harness/rules/verification-skepticism.md`）。**禁止写业务逻辑。**

### 6.1 审查清单

- 测试是否覆盖边界、错误路径、安全（越权/注入）
- 是否存在「为通过而写」的伪测试
- 契约与 `harness-workspace/planning/product-specs/` 验收标准是否一致

### 6.2 破坏性测试

在沙箱内补充攻击用例并执行：

```bash
bash .harness/scripts/run_in_sandbox.sh 'npm test -- --testPathPattern=auth.security'
```

当前 L2/L3 兼容路径在启用独立 QA 时必须记录证据；目标状态由 `finish` 按 `standard/strict` 要求生成或引用：

- TEST 报告：`harness-workspace/evidence/test-reports/<work-item-id>-<Tn>-TEST.md`
- REVIEW 报告：`harness-workspace/evidence/review-reports/<work-item-id>-<Tn>-REVIEW.md`

### 6.3 签章

**通过：**

```bash
bash .harness/scripts/qa_sign_off.sh T1 pass '已补充越权用例；边界 3 项全绿'
```

**驳回：**

```bash
bash .harness/scripts/qa_sign_off.sh T1 fail '缺少未登录访问 /api/admin 的负向用例'
```

凭证路径（有激活任务时）：

- 主路径：`harness-workspace/runs/tasks/<taskId>/qa_approved_<Tn>.json`
- 兼容副本：`harness-workspace/runs/qa_approved_<Tn>.json`

结构见 `.harness/templates/qa-evidence.json`。排障：`bash .harness/scripts/task_workspace.sh qa-path T1`

### 6.4 通知 Lead 跑门禁

```bash
bash .harness/scripts/subagent-pr-gate.sh T1
bash .harness/scripts/qa_evidence_check.sh
```

仅当 `qa_sign_off`、`subagent-pr-gate`、`qa_evidence_check` 均为 `pass` 时，Lead 可将 DAG 标为 `[x]`。

---

## 7. 当前兼容：GC Sweeper

身份：`gc-sweeper`（见 `.harness/rules/gc-golden-principles.md`）。**禁止添加新功能。**

### 7.1 触发扫描

```bash
bash .harness/scripts/memory-sweep.sh T1
```

`block` 时按 `reason` 中的 `AI_COMMENT` / `DEBUG_PRINT` 定位并清理。

### 7.2 清理范围

- `TODO: AI` / `FIXME: agent` 类过程注释
- 业务代码中的 `console.log`、调试 `print`（测试目录除外）
- 死代码、无用 import

### 7.3 复检

```bash
bash .harness/scripts/memory-sweep.sh T1   # 直至 pass
# 仅发现跨任务长期候选时运行 Growth scan / review / apply
bash .harness/scripts/harness_growth.sh scan
bash .harness/scripts/check.sh
```

Growth 不是固定收尾。只有出现新的失败模式、架构边界、默认行为或明确技术债候选时才进入 review；没有候选时不得为“走流程”生成空报告。

---

## 8. 当前 runtime 与 CI 命令参考

在仓库根目录执行：

```bash
# Harness 结构完整性（克隆后/改 .harness 后必跑）
bash .harness/scripts/validate_harness.sh

# 文档断链与陈旧引用
bash .harness/scripts/doc-gardening.sh

# 发布前总检：Planning Gate + validate + structure_guard --diff + plan_sync + dag_sync + qa_evidence + growth_review + quality
bash .harness/scripts/check.sh

# MR 前（产品根 git 状态 + runs 防漏 + check 全链）
bash .harness/scripts/mr_ready.sh --message "feat: 描述"

# 开源发布前，仅发布 harness-engineering runtime 时执行
bash .harness/scripts/release_preflight.sh

# 可选：前端浏览器 QA 环境检查
bash .harness/scripts/browser_qa_setup.sh check
```

`mr_ready` 始终输出绑定当前 diff 的 `review_checklist`（correctness/security/tests/scope）。独立 reviewer receipt 必须位于产品 `harness-workspace/runs/`、绑定相同 subject digest、包含四视角结论，且 reviewer 不能是实现或 Lead 角色。设置 `HARNESS_AGENT_REVIEW_RECEIPT=<path>` 提交 receipt；设置 `HARNESS_AGENT_REVIEW_REQUIRED=true` 后，缺少有效 receipt 会返回 `AGENT_REVIEW_REQUIRED`。未启用 required 时 checklist 仅用于 shadow 数据采集，不能宣称已完成独立 Agent Review。

其他 Git 平台不需要复制一套 Harness 状态机；CI 只需调用同一个 `harness_runtime.py ci-check` 判定器并把结果绑定目标 commit。平台接入未验证 required check、发布依赖和 provider 生命周期前，只能声明 `local` 或 `guarded`。

GitHub 使用 `.github/workflows/harness-required.yml` 生成 `harness-commit-acceptance` check。PR 描述必须包含唯一绑定：

```text
Harness-Task: <task-id>
Harness-Scope: <repo-relative-root-or-.>
Harness-Tier: lite|standard|strict
```

缺失、重复、路径穿越或非法 tier 会直接 block。required workflow 在 PR 阶段判定分支保护所见 commit；PR 合并后再对真实 merge commit 使用同一判定器生成 `harness-result.json` artifact，不读取开发者本地结果。关闭但未合并的 PR 会生成明确失败的 Required run，不能通过跳过 job 触发含糊的下游成功链。`harness init` 只在目标文件缺失时安装 required、release 和 provider-complete workflows，不覆盖产品已有 workflow。

---

## 9. 接入保障等级与目标端到端路径

execution tier 与 assurance level 必须分开理解：前者决定任务需要跑哪些 gate，后者决定无效结果能否进入正式版本。

| 接入等级 | 使用场景 | 日常入口 | 当前状态 |
|----------|----------|----------|----------|
| `local` | 个人、本地 Git、快速试用 | `start/status/finish` | 已可用；结果可审计但可被显式绕过 |
| `guarded` | 小团队、希望低成本阻止误提交/误推送 | 日常入口不变，初始化时安装版本化 guards/CI | 目标设计；尚未实现并验证 |
| `enforced` | 合规、发布或组织级不可绕过准入 | 日常入口不变，平台或受控接受点执行 required check | GitHub live probe 已实现；需按实际平台完成接线验收 |

`guarded` 是轻量推广的默认目标，不要求自建 Gitea/GitLab 或购买 GitHub 套餐；它不能因方便而伪称不可绕过。需要绝对准入时，再选择 protected branch、受控 bare repository、发布 gate 等 `enforced` 承载方式。

目标公开路径只有三步；当前 `local` 接入入口如下（旧结果字段仍显示 `shadow`）：

```bash
bash .harness/scripts/harness start demo-login-task --scope src/auth --work-item <provider-id>
bash .harness/scripts/harness status demo-login-task
bash .harness/scripts/harness finish demo-login-task
```

范围变更和紧急修复仍通过 `start` 建档，不增加公共命令。使用 `--kind scope-change|hotfix --reason '<原因>'`；`scope-change` 最低为 standard，`hotfix` 强制提升到 strict，不能用显式 `--tier lite` 降级，也不豁免 planning、scope、测试、GC 或最终结果不变式。

`standard` / `strict` 命中 GC 要求而没有有效独立结果时，`finish` 返回 `GC_REQUIRED`。`gc_result.json` 必须包含 `role: gc-sweeper`、`independent: true`，并绑定当前 `task_id`、`subject_digest`、`policy_digest`；任一不匹配都不可复用。配置 `HARNESS_GC_AGENT_ARGV`（JSON argv 数组）后可自动调用一次 runner；runner 只接收任务 scope、`changed_since_baseline` 文件的真实 patch/新增文件内容、变更文件列表、一层直接依赖、触发信号、限制和结果契约。上下文超过 `HARNESS_GC_CONTEXT_MAX_CHARS`（默认 200000）或超过一次 Agent 调用预算都返回 `BUDGET_APPROVAL_REQUIRED`，不会静默截断或扩读全仓。

GitHub required workflow 使用内部 `ci_gc_review.py` 对目标 commit 重算 tier 与 GC 信号。lite 或 standard 无信号时返回 `GC_NOT_REQUIRED`，不调用远端；需要独立 GC 时必须配置 repository secrets `HARNESS_GC_REVIEW_URL`（HTTPS）和 `HARNESS_GC_REVIEW_TOKEN`。reviewer 接收任务契约、目标 commit 相对第一父提交的 `commit^1..commit` patch、一层依赖和 subject/policy 绑定；因此 merge commit 不会因默认 diff-tree 行为漏掉实际合入内容。reviewer 返回同一 `gc_result.json` receipt；缺配置、网络失败、receipt 不匹配或上下文超预算都会让 required check fail closed。调用次数、上下文字符数和耗时写入统一 `result.json.cost.harness`。

workspace 兼容命令：

```bash
bash .harness/scripts/harness workspace audit
bash .harness/scripts/harness migrate-task <task-id> --reason '<复核原因>'
bash .harness/scripts/harness enforcement audit
```

`enforcement audit` 使用 `GITHUB_TOKEN` 实时检查目标分支 required check 与远端当前 commit，并通过 Contents API 从该 commit 读取 required/release/provider workflow，禁止用本地 dirty 文件影响判断。repository 可从 GitHub HTTPS、`git@github.com:` 或 `ssh.github.com:443` remote 严格解析，也可由产品 enforcement 配置显式覆盖；非 GitHub host 不会被猜测。随后通过 Actions API 要求 release/provider 成功运行的 `head_sha` 都等于该 commit。目标分支推进后旧运行失效；Required 失败、非目标分支或非 merge 运行都会让下游 workflow 显式失败，不能用 job skip 产生可误认的成功运行。也可用 `--snapshot` 做离线诊断，但 snapshot 会被强制标记为非 live，永远不能把状态升级为 enforced。`status` 每次重新评价一小时有效期的 live probe，不信任旧结果中的 `enforcement` 字符串。

`status` 同时重算当前 worktree subject 与 policy digest。任一输入变化都会把 `validated` 退回 `active`，标记旧 checks 为 stale、增加 rerun 计数并返回 `INPUT_CHANGED`；下一次 `finish` 重新执行当前 tier 所需检查并重算派生 blockers。`result.json` 在原子写入和读取时都执行共享 schema 校验，非法 state/tier/check/cost 或伪造的完整遥测会返回 `RESULT_SCHEMA_INVALID`。

本地 `finish` 只在 subject、policy、相关输入和工具 digest 全部一致时复用确定性的 `tier` 与 `scope` 判定，并把命中数累加到 `cost.harness.cache_hits`。diff code-health 每次都以 `source: executed` 重跑；QA、GC Agent、生产、部署和回滚证据不进入缓存。GC 修改代码后，tier、scope、测试、结构和 code-health 的旧 fingerprint 均失效。CI `ci-check` 始终针对目标 commit 重新执行，不读取本地缓存。

结构化 gate runner 按 tier 将 planning、structure、QA、knowledge、growth 和 quality 分别写入 `checks`。standard 命中 `.tsx/.jsx/.vue/.svelte/.html/.css/.scss` 或 frontend/web/ui/pages/components 路径时自动要求 `HARNESS_BROWSER_QA_URL` 并执行浏览器审计；strict 还要求 `HARNESS_STRICT_EVIDENCE` 指向绑定当前 subject、包含 browser/deployment/rollback pass 的 JSON receipt。产品可在 `quality.commands.lint` 使用 `python-import-boundaries` builtin 声明 `paths` 和 `boundaries: [{from, forbid}]`，通用默认值不内置产品目录。

本地 `work_item.sh close` 默认只写 `ready_to_release`。`done`、`implemented`、`released` 等终态必须在 CI 中传入绑定 work item、merge commit SHA、成功 required run 和 merge/release event 的 `--lifecycle-receipt`；provider workflow 只消费触发它的成功 run artifact，并验证该 SHA 恰好关联一个已合并 PR。普通 Agent 调用会返回 `PROVIDER_TERMINAL_STATUS_FORBIDDEN`。

| 公开动作 | 使用者看到的结果 | 过渡期内部能力参考 |
|----------|------------------|--------------------|
| `start` | 任务身份、初始 execution tier、范围、预算与待办 | 第 2 节的规划/绑定 + `agent_start.sh` |
| `status` | 当前有效 tier、通过项、阻塞项、实现成本和 Harness 开销 | `task_workspace.sh`、各 gate 的 JSON 结果 |
| `finish` | 重新按实际 diff 分级，执行或复用必要检查，写入 `result.json` | 第 4–8 节按角色和风险选择的 gate + `check.sh` / `mr_ready.sh` |

过渡期需要直接调用脚本时，从上表进入对应章节，只执行当前 planning level、角色和风险要求的命令。不要复制一条固定 L2/L3 链给所有任务，也不要在本地验证后直接把外部 Work Item 置为 `done`；目标生命周期是本地 `finish` 最多进入 ready/review，目标 commit 的 CI required check 通过且合并或发布成功后才关闭任务。

---

## 10. 当前兼容错误排查

| 现象 | 处理 |
|------|------|
| `BMAD_GATE_BLOCKED` | 补 `harness-workspace/planning/product-specs`、`harness-workspace/planning/tasks` 目录或 Gate 1；见 [BMAD_Prelude.md](./BMAD_Prelude.md) |
| `NO_PLANNING_GATE` | `check.sh`：先 `planning_gate.sh pass` |
| `PLANNING_GATE_NOT_FOUND` | `agent_start`：未 MR 合并 `harness-workspace/planning/tasks/` 或未 pull；`git pull` 后重试 |
| `PLANNING_GATE_INVALID` | Planning Gate JSON 无效；重跑 `planning_gate.sh` |
| `NO_WORK_ITEM_IN_PLANNING_GATE` | L2/L3 Planning Gate 缺 work_item；重跑 gate 并填 Work Item ID |
| `WORK_ITEM_MISMATCH` | `agent_start` 参数 ≠ `00-任务卡` Work Item ID |
| `WORK_ITEM_GATE` / `*_INVALID_ID` | L2/L3 须匹配当前 provider 的 ID 规则；noop 下 `sync-spec` 会生成语义化本地 ID |
| `VIOLATION_NO_TDD` | 计划须含「测试」/`test`/`TDD` |
| `VIOLATION_NO_DAG` | 计划须含 `[ ]` 勾选语法 |
| `VIOLATION_NO_PATH` | 计划须含白名单业务路径（如 `services/catalog_service/`）或 `structure_guard` |
| `VIOLATION_NO_PLAN_REF` | L2/L3 计划须引用 `03-实施方案` 或任务 ID |
| `PATH_PREFLIGHT_FAIL` | `structure_guard --path` 未 pass |
| `PLAN_SYNC_*` | 本任务相对启动 baseline 的变更路径不在 `03-实施方案`；更新 03 或收窄本任务修改 |
| `PLAN_SYNC_BASELINE_*` | 旧任务缺 baseline 时，由负责人复核计划外 dirty 路径确为任务前变更，再执行 `plan_sync_check.sh --recover-baseline '<复核原因>'`；恢复记录不可覆盖 |
| `TASK_CONTRACT_*` | `03-实施方案.md` 缺 7 字段任务契约、路径或可执行验证 |
| `DAG_SYNC_*` | `tasks-dag.md` 与 `03-实施方案.md` 实现任务 ID 不一致 |
| `STRUCTURE_*` / `UNKNOWN_PROFILE` | 路径不在白名单或显式 profile 不存在；见 `.harness/profiles/<profile>/package-allowlist.yaml`，只有 `generic` 使用 `.harness/rules/package-allowlist.yaml` 兼容副本 |
| `VIOLATION_NO_QA` | 先 `qa_sign_off.sh … pass` |
| `QA_NOT_PASSED` | 凭证为 fail，须修复后重新 QA |
| `QA_EVIDENCE_*` | 补齐 `qa_approved_<Tn>.json`、TEST 报告、REVIEW 报告，并确保报告结论为通过 |
| `GC_DIRTY` | gc-sweeper 按 reason 清理后重跑 `memory-sweep.sh` |
| `PROVIDER_TERMINAL_STATUS_FORBIDDEN` | 本地不能关闭 Work Item；等待 merge/release CI 使用 lifecycle receipt 写终态 |
| `NOT_ENFORCED` | 查看 `status.result.enforcement_probe.blockers`；required check、release dependency、provider completion 或 live probe 至少一项未验证 |
| `INPUT_CHANGED` | 上次 validated 后代码或 policy 已变化；重新运行 `finish`，旧 fingerprint 不再有效 |
| `RESULT_SCHEMA_INVALID` | result 字段、枚举、check 最小字段或成本类型不符合共享 schema；须由 Harness 重建或迁移 |
| `HARNESS_INVALID` | 按 reason 补齐 manifest 缺失项 |
| `MR_TASK_DIR_UNKNOWN` | 一 MR 一 `harness-workspace/planning/tasks/<id>/`，或设 `HARNESS_TASK_DIR` |
| `list-mine` 为空 | noop 下恒空；Teambition/Jira 检查任务是否指派给当前账号；飞书检查 `tasklist_guid` / `list_query` 与 `FEISHU_ASSIGNEE_ID` 是否匹配 |
| 受控命令仍 block | 读 `CONTROLLED_EXEC_FAILED` 日志，必要时 `gstack_investigate.sh` |
| `DOCKER_SANDBOX_UNAVAILABLE` | 本机/CI 未安装 Docker，或 Docker daemon 不可用；改用默认 controlled 后端或配置 Docker runner |
| `REMOTE_SANDBOX_CONFIG_*` / `REMOTE_SANDBOX_SUBJECT_MISMATCH` | remote backend 缺少 HTTPS endpoint、workspace ref/token，或 executor 返回的 subject 与目标不一致；不能回退伪造 pass |
| `PLAYWRIGHT_*` | 运行 `browser_qa_setup.sh check` 定位；本地可用 `install --install-package`，CI 推荐 Playwright 官方镜像 |
| `QUALITY_*_UNCONFIGURED` | 产品侧 `harness-workspace/project.yaml` 缺 `quality.commands.lint/test`，或严格 CI 未显式豁免 |

完整错误码见 [Harness_全景手册.md](./Harness_全景手册.md) §18.3。

---

## 11. 文档索引

| 文档 | 用途 |
|------|------|
| [Harness_全景手册.md](./Harness_全景手册.md) | **全景：设计·方案·评估·演进** |
| [Team_Product_Harness.md](./Team_Product_Harness.md) | 通用产品研发 Harness 模型 |
| [USAGE.md](./USAGE.md) | **本文件：三入口使用模型与过渡期兼容参考** |
| [COLLABORATION.md](./COLLABORATION.md) | 多人 PM/Dev 协作 |
| [BMAD_Work_Item_Contract.md](./BMAD_Work_Item_Contract.md) | BMAD Planning 到 Teambition/飞书/Jira 的同步契约 |
| [Harness_Workflow.md](./Harness_Workflow.md) | 双闭环 + Work Item provider 总览 |
| [BMAD_Prelude.md](./BMAD_Prelude.md) | BMAD Planning · BMAD Method 前导 + `bmad_method_gate.py` |
| [QUALITY.md](./QUALITY.md) | 质量不变式 |
| [../AGENTS.md](../AGENTS.md) | Agent 导航地图 |
| [../.harness/README.md](../.harness/README.md) | 脚本与机制索引 |
