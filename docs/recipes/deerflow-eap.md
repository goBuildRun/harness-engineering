# DeerFlow/EAP Product Recipe

本 recipe 描述 DeerFlow-based EAP 这类产品如何叠加在通用 Team Product R&D Harness 之上。它不是 harness core 的必经步骤；其它产品只需要 `harness_init.sh init` 和产品侧 `harness-workspace/project.yaml` 即可接入。

统一入口落地后，recipe 只能向 `harness plan/start/status/finish` 提供产品识别、结构 allowlist、上下文检索和附加 gate；不得新增平行完成态，也不得要求使用者手工串联一套 DeerFlow 专属必经流程。下面命令是当前 bootstrap 兼容说明，不是每次产品迭代的操作路径。

## 分层

```text
1. harness core
   harness_init.sh init/use/list/remove
   创建产品侧 harness-workspace/，登记产品台账，设置 active product fallback，安装/校准 BMAD。

2. product bootstrap
   eap_init / deerflow_product_init
   克隆或登记 DeerFlow，生成 agent blueprint，选择 capability package，写 adoption note。

3. product capability delivery
   capability package 逐步实现
   具体能力包、契约、PoC、前后端适配和产品测试全部属于产品仓库。
```

## 通用初始化

```bash
cd /path/to/harness-engineering
BIN=.harness/scripts

bash "$BIN/harness_init.sh" init \
  --product-root /path/to/eap-product \
  --product-id eap-platform \
  --product-name "EAP Platform" \
  --profile generic \
  --work-item-provider noop \
  --install-bmad
```

如果团队有专门的 EAP profile，可以把 `--profile generic` 换成该 profile；profile 只应该提供领域模板、结构 allowlist 和参考上下文，不应该改变 harness core 的产品根解析或 workspace 契约。

## EAP/DeerFlow 后续步骤

完成通用初始化后，再执行产品自己的 bootstrap：

```text
eap_init / deerflow_product_init
capability package selection
agent blueprint generation
adoption note / capability gates
```

这些步骤应写入产品仓库自己的文档、脚本或 `harness-workspace/planning/` 产物。harness-engineering 只负责读取产品 workspace、执行门禁和同步证据。

## 不变式

- `enterprise-agent/` 只是一个产品根示例，不是 harness-engineering 的默认根。
- DeerFlow clone、agent blueprint、capability package 和 adoption note 都是产品产物。
- 产品长期配置写入 `<product-root>/harness-workspace/project.yaml`。
- 本机产品上下文优先由 `--product-root/--product-id`、`HARNESS_PRODUCT_ROOT/HARNESS_PRODUCT_ID` 或 cwd discovery 决定；`.harness/products/active-product.json` 只是默认兜底。
- 通用脚本不得假设产品必须包含 DeerFlow、capability package 或 EAP 架构目录。
- recipe gate 必须写入同一个任务 `result.json` 并服从 execution tier、fingerprint、成本遥测和 Git attestation 绑定规则，不生成平行完成态。
