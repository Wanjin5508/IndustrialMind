# HTTP API

Owner: Engineering · Status: M2 implemented · Updated: 2026-11-09

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

## M2 文件接口

下列读取接口允许两种演示角色，写入接口要求工程师。全部契约见生成的 OpenAPI。

| 接口 | 载荷与行为 |
|---|---|
| GET `/capabilities`、`/fixtures` | 配置状态（无密钥）和开发样例目录 |
| POST `/runs/from-fixture/{case_id}` | 读取实际文件创建任务，不读答案 |
| POST `/runs/upload` | multipart：rfq、drawing |
| POST `/runs/{id}/analyze` | expected_version、mode（local_text 或 live） |
| PATCH `/runs/{id}/fields` | expected_version、values 字典、reason；保留原值及来源 |
| POST `/runs/{id}/drawing` | multipart：drawing、expected_version、reason；需重新抽取 |
| GET `/runs/{id}/documents/{document_id}/pages/{page}` | 任务内 PDF 页图 PNG；页号从 1 开始 |
