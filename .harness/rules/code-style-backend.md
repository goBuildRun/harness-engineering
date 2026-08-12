# 后端代码风格与实施约束

> **沙箱 TDD 命令**：[docs/USAGE.md](../../docs/USAGE.md) 第 4 节。

## 分层（见 ARCHITECTURE.md）

Types → Config → Repo → Service → Runtime；禁止 UI 层直接访问 Repo。

## TDD 纪律

1. 先写失败测试（红）
2. 最小实现（绿）
3. 重构（保持绿）
4. 计划须过 `feedback_planner.sh`

## 数据边界

在 API/消息边界解析并校验数据形状；实现库自选，行为须可测试。

## 命名

- 类型/接口：`PascalCase`
- 函数/变量：`camelCase`（Go 用导出规则）
- 测试文件：`*_test.go` / `*Test.java` / `test_*.py`

## 执行

所有构建与测试：

```bash
bash .harness/scripts/run_in_sandbox.sh 'mvn test'   # 示例
```

## 完成前自检

- [ ] 测试覆盖新增路径
- [ ] `structure_guard.sh --diff` 已通过
- [ ] 变更路径已在 `03-实施方案` 登记
- [ ] 无调试打印残留
- [ ] 未自签被要求的独立验收；需要 QA 时已交给 `qa-evaluator`
