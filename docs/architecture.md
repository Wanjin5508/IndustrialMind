---
owner: Engineering
status: as-built-M4
updated: 2026-11-10
code_baseline: 196feb7
---
# 当前架构入口

完整架构与基本原理集中维护在 [[IndustrialMindAI-项目手册]]：第 3 节模块职责，第 4 节调用时序，第 6–9 节证据、知识、计价审批与状态机。面试展示用 [[docs/面试与演示讲解]]，避免此处重复维护同一张主图。

当前结构是 Next.js + FastAPI 模块化单体，SQLite 保存任务快照和审计，文件目录保存原始资料与页图；模型为抽取 Provider，知识查询和成本判断由后端确定性规则控制。

已接受的决策：

- [[docs/adr/001-local-modular-monolith|ADR 001：本地模块化单体]]。
- [[docs/adr/002-evidence-and-model-accounting|ADR 002：证据强度与模型用量]]。
- [[docs/adr/003-controlled-knowledge|ADR 003：受控知识与执行选择]]。

当前没有统一自然语言 Orchestrator、企业连接器、队列自动恢复或生产身份；对应 roadmap M5 及以后阶段。该边界同时约束演示话术与未来架构图。
