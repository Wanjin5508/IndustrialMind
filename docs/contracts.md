# 数据契约

Owner: Engineering · Status: M0 baseline · Updated: 2026-11-08

契约实现：`apps/api/src/rfq_workbench/contracts.py`；导出文件：`docs/schemas/*.json`。

| 对象 | 语义与不变量 |
|---|---|
| ConfirmedInputs | 人工明确确认的 M1 输入；数量为正整数；不得冒充文件抽取 |
| Extraction | RFQ 与 drawing 分开提取，保留版本冲突与空值 |
| Source | 解析器生成 ID、document_id、从 1 开始的页码与可选归一化 bbox |
| Issue | code、field、severity、可操作的描述；blocking 未解决时不批准 |
| Quote | 十进制金额、输入与参考快照 hash、模板/费率/规则/草案版本、未计入项 |
| Approval | 绑定具体输入和草案版本，由后端授权动作产生 |
| AuditEvent | 单调序号、时间、操作人、动作与变化摘要 |

坐标采用左上角为原点的 `[x0,y0,x1,y1]`，范围 0–1。来源只证明出处，不能单独证明工程适用性。扫描页可仅使用页级证据并要求人工核查。

金额规则：`unit_cost = variable_cost + setup_cost / quantity`；`unit_price = ROUND_HALF_UP(unit_cost / (1-margin), 2)`；`total = rounded_unit_price * quantity`。报价仅为受限内部预算草案，不含未知运输、税费等。

变更请求携带 `expected_version`；过期写入返回冲突。输入或参考依赖发生变化后，不能继续使用旧审批导出。

## M3 证据契约

新增 `EvidenceCheck` 与 `ReviewedField`，schema 在 docs/schemas。`source_exists` 与 `value_supported` 分开；`human_confirmed` 不改写原始证据结论。未知标记规范化为 null，字段最长 200 字符。定位分 text/page；当前 PDF bbox 为 null。模型调用 metrics 可包含服务主机、用量、估算费用和完整费率快照。历史记录不回填不存在的测量。

## M4 知识契约

新增 KnowledgeRecord、KnowledgeQuery；Quote 增加 knowledge_basis 与 routing。reference_hash 从单文件 hash 升级为制造载荷与选中知识快照的组合指纹。旧草案在下一批准/导出时可能触发 STALE_DRAFT，须重新计价审批。
