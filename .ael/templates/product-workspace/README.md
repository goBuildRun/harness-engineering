# {{product_name}} AEL Workspace

本目录是 **{{product_name}} 产品侧 AEL workspace**，归产品仓库所有。它保存本产品的 AEL 配置、规划产物、运行状态、长期知识与交付证据。

buildrun-agent-engineering-lifecycle runtime 不放在这里。buildrun-agent-engineering-lifecycle 可以位于任意路径，通过 `ael_init.sh init/use` 绑定本产品根目录。

> `standard/strict` 正常路径是 `ael plan → start → status → finish`；低风险 `lite` 可省略显式 `plan`。当前 lifecycle runtime 已提供统一门面，下面命令仅保留初始化、诊断和兼容用途。结果中的 `enforcement: shadow` 只是旧字段兼容值，不代表当前 assurance 等级。目录语义继续保留，但 TEST、REVIEW、SUMMARY、PROGRESS、GROWTH 等产物按 execution tier、任务恢复或长期候选生成，不要求每个任务全部创建。

## 1. Workspace 结构

```text
{{workspace}}/
├── project.yaml          # 产品接入 buildrun-agent-engineering-lifecycle 的配置真相源
├── planning/             # 产品规格、执行计划、任务包
│   ├── product-specs/
│   ├── exec-plans/
│   └── tasks/
├── runs/                 # 本地运行状态、active task、QA 凭证、浏览器 QA 证据
├── knowledge/            # 产品级长期知识
│   ├── CONTEXT.md
│   ├── LESSONS.md
│   └── REFERENCE_SYSTEMS.md
└── evidence/             # 接入、任务证据与成长报告
    ├── summaries/
    ├── progress/
    ├── test-reports/
    ├── review-reports/
    ├── intake-reports/
    └── growth-reports/
```

## 2. 边界

- buildrun-agent-engineering-lifecycle 的脚本、模板、规则、Agent 配置不放在这里。
- 产品规格、任务包、测试/审查报告、成长报告放在这里。
- 产品级 `CONTEXT.md` / `LESSONS.md` / `REFERENCE_SYSTEMS.md` 只服务 {{product_name}}。
- 跨产品通用经验必须人工 review 后再沉淀到 buildrun-agent-engineering-lifecycle。
- `runs/` 是运行状态与凭证区，init 会生成 `runs/.gitignore`，除排障外不要手改，也不要把运行状态提交到 MR。
- 业务 lint/test 命令在 `project.yaml` 的 `quality.commands` 中声明。
- TDD 走 buildrun-agent-engineering-lifecycle `run_in_sandbox.sh`；需要容器隔离时可切 Docker backend。

## 3. 新人上手

前置：

| 前置 | 用途 |
|------|------|
| Git | 分支、MR、任务产物入库 |
| Node.js + npm | 由 `ael_init.sh init --install-bmad` 在产品根非交互安装 BMAD |
| Python 3.9+ | buildrun-agent-engineering-lifecycle 门禁脚本 |
| bash 3.2+ | buildrun-agent-engineering-lifecycle shell 入口 |

第一天只需要记住两个根：

| 根目录 | 作用 |
|--------|------|
| `PRODUCT_ROOT` | {{product_name}} 产品仓库根，包含产品代码、架构文档与 `{{workspace}}/` |
| `AEL_ROOT` | buildrun-agent-engineering-lifecycle `buildrun-agent-engineering-lifecycle/` 根 |

示例：

```bash
PRODUCT_ROOT=/path/to/{{product_name}}
AEL_ROOT=/path/to/buildrun-agent-engineering-lifecycle
```

## 4. 当前兼容：首次接入与 L2 示例

buildrun-agent-engineering-lifecycle 绑定产品、安装 BMAD 并校准 BMAD 输出路径：

```bash
cd "$AEL_ROOT"
bash .ael/scripts/ael_init.sh init \
  --product-root "$PRODUCT_ROOT" \
  --product-id {{product_id}} \
  --product-name {{product_name}} \
  --profile {{profile}} \
  --install-bmad

bash .ael/scripts/ael_init.sh use --product-id {{product_id}}
bash .ael/scripts/ael_init.sh list
python3 .ael/scripts/workspace_paths.py json
```

BMAD 安装在产品根 `_bmad/`；BMAD 原生输出由 init 校准到 `{{workspace}}/bmad-output/`；正式 BMAD Planning 产物映射到 `{{workspace}}/planning/`。默认安装 BMAD `bmm,tea` 模块与 `codex,cursor` 工具，并标准化创建 `planning-artifacts/`、`design-artifacts/`、`implementation-artifacts/`、`test-artifacts/`。如果当前 BMAD 版本需要额外设计模块，可用 `AEL_BMAD_MODULES` 覆盖；如果安装结束后没有生成 `_bmad/`，init 会阻断。

全新项目的首批长期上下文来自 BMAD Planning。产品规格、产品蓝图、架构/执行计划和任务边界进入 `{{workspace}}/planning/` 后，运行：

```bash
bash .ael/scripts/ael_knowledge.sh sync-planning
```

`planning_gate.sh` 通过时会自动执行这一步，把规划事实写入 `{{workspace}}/knowledge/CONTEXT.md` 的受管区块。

如果 {{product_name}} 是已有代码和文档的项目，先生成接入报告：

```bash
bash .ael/scripts/ael_intake.sh status
bash .ael/scripts/ael_intake.sh scan
bash .ael/scripts/ael_intake.sh apply-review
```

报告位于 `{{workspace}}/evidence/intake-reports/`。它只提供候选证据；人工或 Agent review 后运行 `apply-review`，由 AEL 把受管区块写入 `knowledge/CONTEXT.md`、`knowledge/LESSONS.md` 与 `knowledge/REFERENCE_SYSTEMS.md`。如果仓库依赖 DeerFlow、OpenViking 这类重度上游系统，review 阶段需要基于报告中的关键代码入口深读源码，并把核心机制、扩展点、禁改边界写入 `REFERENCE_SYSTEMS.md` 的人工维护区。

Work Item provider 在 `{{workspace}}/project.yaml` 的 `work_item` 中声明。长期配置放在产品侧；`.env` 只放 Teambition、飞书或 Jira 的本机密钥。

BMAD Planning 到 Work Item 使用 `bmad-work-item-v1`：先在 Codex 中运行 `draft-spec` 生成待确认任务草稿，确认标题、范围和负责人后，再运行 `sync-spec --assignee <provider-user-id>` 创建外部任务。外部任务只保存摘要、负责人、状态、讨论和 AEL Links；完整规格、执行计划和任务包仍以 `{{workspace}}/planning/` 为真相源。详见 buildrun-agent-engineering-lifecycle `docs/BMAD_Work_Item_Contract.md`。

以下 BMAD Planning 建档是当前 L2 兼容示例，不是所有任务的固定命令链。L1 可走 Quick Flow；目标 `lite` 只生成最小任务绑定：

```bash
cp .ael/templates/product-spec.md "$PRODUCT_ROOT/{{workspace}}/planning/product-specs/某功能.md"
bash .ael/scripts/work_item.sh draft-spec "$PRODUCT_ROOT/{{workspace}}/planning/product-specs/某功能.md" --assignee <provider-user-id>
bash .ael/scripts/work_item.sh sync-spec "$PRODUCT_ROOT/{{workspace}}/planning/product-specs/某功能.md" --assignee <provider-user-id>

TASK_DIR="$PRODUCT_ROOT/{{workspace}}/planning/tasks/$(date +%Y-%m-%d)-<work-item-id>-简称"
mkdir -p "$TASK_DIR"
cp tasks/_templates/00-任务卡.md tasks/_templates/03-实施方案.md tasks/_templates/04-实施记录.md "$TASK_DIR/"

# 编辑 00：Gate 1 → 已确认；填 product-specs/、exec-plans/active/ 链接
bash .ael/scripts/planning_gate.sh L2 "$TASK_DIR"
```

通过后检查 `{{workspace}}/knowledge/CONTEXT.md`，应能看到 `BMAD Planning 规划沉淀` 区块。

当前 Lifecycle Execution 启动：

```bash
bash .ael/scripts/agent_start.sh <work-item-id>
```

脚本 stdout 是 JSON；终端直跑默认 pretty，脚本捕获/CI/管道保持单行 raw JSON。遇到 `block`，按 `reason` 修复后重跑。需要强制 raw 输出时设置 `AEL_PRETTY=0`。

常用质量命令：

```bash
bash .ael/scripts/run_in_sandbox.sh 'python3 -m unittest discover -s tests'

AEL_SANDBOX_BACKEND=docker \
AEL_SANDBOX_IMAGE=python:3.11-slim \
bash .ael/scripts/run_in_sandbox.sh 'python3 -m unittest discover -s tests'

bash .ael/scripts/browser_qa_setup.sh check
```

## 5. 阅读顺序

| 顺序 | 文档 | 用途 |
|:----:|------|------|
| 1 | 本文 | 产品侧 workspace 与新人路径 |
| 2 | buildrun-agent-engineering-lifecycle `AGENTS.md` | Agent 地图 |
| 3 | buildrun-agent-engineering-lifecycle `ARCHITECTURE.md` | buildrun-agent-engineering-lifecycle / product workspace 边界 |
| 4 | buildrun-agent-engineering-lifecycle `docs/getting-started/cli.md` | 主动作与只读 status 的使用模型及兼容参考 |
| 5 | buildrun-agent-engineering-lifecycle `docs/getting-started/brownfield-intake.md` | 已有项目接入、INTAKE 报告与 review 规则 |
| 6 | buildrun-agent-engineering-lifecycle `docs/planning/bmad-planning.md` | BMAD 与 planning 映射 |
| 7 | 产品架构文档 | 产品服务边界、目录结构、数据库和部署约束 |

## 6. 常见误区

1. 在 buildrun-agent-engineering-lifecycle 目录手动执行 `npx bmad-method install`。BMAD 应通过 `ael_init.sh init --install-bmad` 指定产品根，或在产品根手动执行。
2. 把任务包建在 buildrun-agent-engineering-lifecycle `tasks/`。任务产出必须在 `{{workspace}}/planning/tasks/`。
3. 把一次聊天当成记录系统。稳定结论必须写入 `planning/`、`knowledge/`、`evidence/` 或产品架构文档。
4. 让实现 Agent 自签完成。任务必须经过最终门禁；独立 QA、TEST/REVIEW 与生产证据按 execution tier 启用，不能由实现者伪造或自行签署。
5. 把已有项目扫描报告直接当成知识库。`intake-reports/` 只是候选证据，必须人工或 Agent review，并把语义级摘要沉淀到 `knowledge/`。
