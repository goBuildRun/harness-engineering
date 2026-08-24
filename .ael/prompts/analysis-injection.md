# 分析阶段注入（Lead → 子 Agent，只分析不修改）

## 硬约束

- **只分析，不修改**任何业务代码文件
- 不写测试、不跑会改文件的构建
- 输出结构化结论供 Lead 写入 `02-影响分析.md`

## 须阅读

- 当前产品 `ael-workspace/project.yaml` 与任务规格
- 当前 active profile 的 `docs/references/<profile>/index.md`（存在且与任务相关时）
- 产品侧 `knowledge/CONTEXT.md` / `REFERENCE_SYSTEMS.md` 中与本任务路径相关的条目

## 输出格式

```text
A. 涉及服务/模块
B. 建议修改路径（须在白名单内）
C. 契约变化
D. 风险
E. 验证思路
```

## 禁止

- 建议 active profile allowlist 外的路径，或把某个 profile 的目录当成通用默认值
- 勾选 DAG 为完成
