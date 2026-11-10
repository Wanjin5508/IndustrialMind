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

## M4 受控知识 API

`POST /knowledge/search` 需要任一演示角色；接收 KnowledgeQuery，默认空 query 查全部。权限范围由服务端固定，不接受 project_id、plant 或角色覆盖。`GET /knowledge/{record_id}` 使用默认 B 版铝件条件判断适用性；未知/越权均 404。返回 eligible_for_cost、reasons、usage、source_id、record_hash。价格路径通过服务器唯一选用三类依据，无法由客户端提交 ID 覆盖。

## M5 统一任务 API

| 接口 | 角色与契约 |
|---|---|
| POST `/tasks` | TaskRequest：intent 可空，request 为上下文文字，query 可空，fixture_id 可选；返回 201，仅创建，不调用模型 |
| POST `/tasks/{id}/resume` | ResumeTask：expected_version、可选 intent/query、mode 默认 local_text；每次最多一次抽取和一次计算或查询 |
| POST `/tasks/{id}/documents` | 工程师；multipart expected_version、rfq/drawing 至少一份；支持分次补件；清除旧字段、报价、审批及结果 |

意图为 prepare_quote / review_drawing / search_knowledge。前两类仅工程师创建和执行；知识任务任一角色可创建，但列表、详情、恢复仅同一创建角色可访问。无意图任务也按创建角色隔离。确定目的后不能更改；未知文本保持澄清。知识查询允许 query={} 显式浏览默认条件下的获准资料。

旧任务继续使用 /runs 分步接口；没有 orchestration 的任务调用任务恢复/补件返回 LEGACY_TASK。旧 cost/approve/export 接口拒绝非报价意图（INTENT_BOUNDARY），不能靠切换前端页签升级权限。目的转换返回 INTENT_IMMUTABLE/409；旧版本恢复返回 VERSION_CONFLICT/409。缺件和工程暂停是正常任务响应，分别保留 CLARIFICATION_REQUIRED/NEEDS_ENGINEERING_REVIEW 与结构化 pause；模型调用错误仍采用现有稳定错误响应。

示例：POST /tasks 传 {"intent":"prepare_quote","fixture_id":"DEV-001"}，使用返回版本 POST /tasks/{id}/resume。不要硬编码 version；读取响应中的最新值。生成的 OpenAPI 与 TaskRequest/ResumeTask schema 是机器契约。
