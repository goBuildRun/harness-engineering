# 前端代码风格与实施约束

> **沙箱与 browser_qa 命令**：[getting-started/cli.md](../../docs/getting-started/cli.md) 第 4.4 节。

## 分层

Types → Config → Service → Runtime → UI；业务逻辑勿堆在页面组件内。

## TDD / 验证

- 单元测试：组件与纯函数优先
- 集成/UI：`.harness/scripts/browser_qa.py` 或 Playwright（见 TD-002）

## 可访问性

交互元素须有可辨识标签；破坏性测试由 `qa-evaluator` 执行。

## 执行

```bash
bash .harness/scripts/run_in_sandbox.sh 'npm test'
python3 .harness/scripts/browser_qa.py http://localhost:3000 --action audit
```

## 完成前自检

同 `code-style-backend.md`：测试、无调试残留；当前任务要求独立 QA 时再检查 QA 签章。
