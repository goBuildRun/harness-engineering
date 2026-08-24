# 实施阶段注入模板（Lead → backend/frontend-agent）

复制到子 Agent prompt 的「本次任务须遵循的规则」段。

## 结构守门（必选）

- 写**任何**业务文件前：`bash .ael/scripts/structure_guard.sh --path <完整相对路径>`
- 写完后：`bash .ael/scripts/structure_guard.sh --diff`
- 路径权威：当前产品 active profile 的 package allowlist
- 不变式：产品架构、任务契约，以及存在时的 `docs/references/<profile>/INVARIANTS.md`

## 放置

- 仅可在 active profile 白名单根下新建文件
- 遵守产品已有模块分层和依赖方向，不假设所有产品都使用同一目录模型
- 变更路径必须出现在任务契约 `write_files` 中

## TDD

- 沙箱：`bash .ael/scripts/run_in_sandbox.sh '<test>'`
- 连续失败：`bash .ael/scripts/gstack_investigate.sh '假说…'`

## 完成

- 不得自签 QA；向 Lead 汇报**变更路径列表**
