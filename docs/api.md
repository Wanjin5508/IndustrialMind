# HTTP API

Owner: Engineering · Status: M1 implemented · Updated: 2026-11-08

Base URL: `http://127.0.0.1:8000`。交互文档 `/docs`；机器契约 [OpenAPI](schemas/openapi.json)。

认证头 `X-Demo-Token`：本机默认 `demo-engineer` 或 `demo-approver`，可在环境中覆盖。它们是公开的演示令牌，不是生产身份系统；服务仅绑定本机。

| 方法与路径 | 角色 | 行为 |
|---|---|---|
| GET `/health` | 公开 | 健康与演示范围 |
| GET `/runs`、`/runs/{id}` | 两种角色 | 项目内任务与审计 |
| POST `/runs/confirmed` | engineer | 从明确人工输入创建 M1 任务；模式为 MANUAL_CONFIRMED |
| PATCH `/runs/{id}/inputs` | engineer | 带 reason 与 expected_version 修改，旧结果失效 |
| POST `/runs/{id}/cost` | engineer | 受控模板与 Decimal 计算 |
| POST `/runs/{id}/approve` | approver | 确认技术字段和 internal_only，批准准确快照 |
| POST `/runs/{id}/export` | approver | 返回带 DRAFT 标识的 Markdown、JSON 报价与审批记录 |

变更操作携带 `expected_version`；冲突返回 409。业务错误格式 `{"error":{"code":"...","message":"..."}}`；请求 schema 错误遵循 FastAPI 422。不存在或不属于当前项目的任务返回 404。

参考费率过期或 hash 改变时，旧报价和审批在批准/导出检查中失效，并追加 `dependency_invalidated` 事件。该检查在使用边界发生，不宣称后台持续监控文件。
