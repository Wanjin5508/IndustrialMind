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
