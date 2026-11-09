---
title: RFQ 工作台技术文档入口
status: active
owner: engineering
updated: 2026-11-08
---

# RFQ 工作台技术文档

权威实施范围见 [[IndustrialMindAI-OptionB-RFQ工作台-roadmap]]；数据策略见 [[IndustrialMindAI-OptionB-输入数据准备计划]]。本目录管理可追溯的设计、契约、运行与验收证据，不能把计划状态写成已通过。

| 文档 | 用途 |
|---|---|
| [开发规范](development.md) | 本地环境、代码/注释、分支、提交、评审和验证规范 |
| [架构](architecture.md) | 模块职责、数据流、状态与部署边界 |
| [契约](contracts.md) | 输入、金额、证据、版本与 API 约定 |
| [API](api.md) | 接口、角色、错误与并发约定 |
| [运行手册](runbook.md) | 启动、配置、故障处理、演示重置 |
| [测试策略](testing.md) | 风险、测试层级、验收命令与模型边界 |
| [里程碑记录](milestones.md) | M0–M2 状态、分支、证据和未解决项 |
| [ADR 索引](adr/README.md) | 重要技术决策与变更原因 |
| [变更日志](../CHANGELOG.md) | 面向使用者的版本行为变化 |

- [M2 验收证据](acceptance-m2.md)：实测、待完成门槛与限制。
- [核心演示](demo.md)：正常、冲突、修改与审批失效。

## 文档管理约定

1. 需求、实现、测试和记录使用 M0/M1/M2 与规则 ID 关联；每个变更同时更新受影响文档。
2. 文档有明确负责人、状态、更新时间。ADR 追加新决策并标记替代关系，保留历史。
3. `docs/baselines/` 是本次开发开始时已确定计划的快照，记录 SHA-256；根目录 roadmap 仍是执行入口。
4. 可执行契约由 Pydantic 导出 JSON Schema，API 上线后导出 OpenAPI；生成文件不得手工改写。
5. 凭据、原始敏感客户资料、运行数据库和服务日志不进入 Git。验收报告只记录脱敏的配置与结果。
6. “通过”需要真实证据；本地文本抽取和测试替身不能代替 M2 的真实多模态调用。
