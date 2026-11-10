# Changelog

All outputs are synthetic internal budget drafts, not production instructions.

## Unreleased

### M1
- Add versioned SQLite tasks and transactional audit events.
- Add deterministic Decimal costing, role-gated approvals, stale-dependency invalidation and internal draft export.
- Add independent business/API tests, OpenAPI export and local operating instructions.

### M0
- Establish domain contracts, synthetic input packages, source manifests and isolated evaluation truth.
- Establish development conventions, ADRs, technical documentation and milestone branch workflow.

## M2 local core — 2026-11-09

新增四视图工作台、实际 PDF/文本解析、页图证据、人工修正与替换图纸、模型 Responses 适配器、启动脚本和 CI。31 项测试与生产构建通过；真实多模态验收尚待凭据。

## M3 — 2026-11-09

完善字段与证据契约、原文匹配及冲突阻断、扫描页人工确认、未知值规范化、模型配置与安全错误码、耗时/token/费用快照。新增界面证据状态、模型调用记录和原尺寸页图入口。真实 DeepSeek 三开发样例通过。

## M4 — 2026-11-10

新增权限范围内的知识查询、批准/期限/适用条件判断、唯一执行依据、知识快照失效和工艺路线展示。补充零件及批准版次规则、计价时几何重检；77 项测试及生产构建通过。

## 项目手册与演示材料 — 2026-11-10

新增完整 M4 项目手册、面试模块讲解、12 分钟与 3 分钟脚本、常见追问和故障备用；提供 7 张可维护 Mermaid 图。手册嵌入演示文档并链接 ADR、运行与验收记录。

## M5 — 2026-11-10

新增三个显式任务入口、持久化澄清、分次补件、人工暂停恢复、单图纸审查及查询结果快照。所有报价/审批接口检查意图边界；知识任务按创建角色隔离；重复恢复保留有效批准。新增 18 项自动验收，累计 95 项通过，三入口浏览器验证完成；更新项目手册、演示脚本、ADR 与生成契约。

## M6 — 可靠性与集成预览

- 识别支持持久化重试上限、超时演练和过期执行恢复。
- DEV-001 支持回放已保存的抽取记录。页面、审计和导出草案标明来源。
- 成本页可加载 PLM 版次、ERP 草案、MES 工时的模拟预览。
