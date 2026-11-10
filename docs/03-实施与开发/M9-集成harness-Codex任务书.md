---
owner: Engineering
status: implemented-M9-simulated
updated: 2026-11-11
baseline: codex/m8-solution-business-case @ 3d28ab9
target_branch: codex/m9-integration-harness
---

# M9 任务书：外部系统读写的准确性与幂等 harness

实施与本地验收已完成，证据见 [[docs/03-实施与开发/09-阶段验收/11-M9集成Harness]]，设计差异见 [[docs/03-实施与开发/08-工程决策/05-集成Harness]]。以下保留原任务范围与验收要求。

> 给 Codex 的执行文档。按 T1→T8 顺序实现，每个任务一个提交。与代码不符之处以代码为准，并在 ADR 中记录差异。
> 本文引用的文件名和函数名来自 M8 基线的静态阅读，未经运行验证。动手前先跑一遍现有测试，确认基线全绿。

## 0. 工作方式

- 新建分支 `codex/m9-integration-harness`。
- 每个任务完成后运行：
  ```
  python -m ruff check apps/api scripts tests
  python -m ruff format --check apps/api scripts tests
  python -m pytest -q
  python scripts/evaluate_m7.py --output var/m7/ci
  ```
  M9 评测脚本落地后（T7）追加 `python scripts/evaluate_m9.py --output var/m9/ci`。
- 修改公开契约后运行 `python scripts/export_schemas.py && python scripts/export_openapi.py`，并提交 `docs/04-项目介绍与展示/09-接口模式/` 的变化（CI 会 `git diff --exit-code` 该目录）。
- 不做与本任务无关的重构。不改既有错误码的含义。

## 1. 背景与目标

M8 之前的系统是"模型只抽取，确定性代码计价，人工审批，导出 Markdown/JSON"。PLM、ERP、MES 只是 `integrations.py` 里的**只读模拟预览**，没有真实读取，也没有写路径。

M9 要补上"系统闸"：任何进出外部系统的数据，都经过确定性管道，具备**来源凭证、幂等、重试语义、写后对账**。目标是能用可重复的故障注入评测证明：

- 目标系统里**不会出现重复草案**，也不会**丢写**；
- 批准失效或依据过期后**不会外发**；
- 结果未知（超时、崩溃）时**先查询再决定**，不盲目重发；
- 写入后会**回读并逐字段对账**，不一致不得标记为成功。

范围声明：目标系统仍是**模拟**的（有状态的假系统，语义贴近真实）。界面和文档保持 `SIMULATED / NO EXTERNAL WRITE` 标注，不得宣称已对接真实 ERP。

### 非目标

真实连接器、企业身份、消息队列或独立 worker 基础设施、PLM 写入、MES 写入、CAD 解析、让模型决定或发起任何写操作、物理删除外部记录（补偿只做取消草案，且为 P2）。

## 2. 必须保留的现有行为（锚点）

| 位置 | 行为 | 约束 |
|---|---|---|
| `repository.py` `Repository.update(run_id, expected_version, actor, action, change, project)` | `change(body)->detail` 在 `BEGIN IMMEDIATE` 事务内执行；成功后 `version+1` 并追加事件 | **外部 I/O 不得进入 `change`**。可新增变体，但保持现有签名可用 |
| `file_workflow.py` `FileWorkflow.analyze` | claim（写租约）→ 事务外调用 → complete；迟到结果得到 `VERSION_CONFLICT` | 出站派发沿用同一模式 |
| `costing.py` `fingerprint()` | `json.dumps(sort_keys, separators, ensure_ascii=False)` 的 SHA-256 | 不改变既有输入的哈希值 |
| `contracts.py` `Quote`（`extra="forbid"`）与 `approval.quote_hash = fingerprint(quote)` | 批准绑定报价哈希 | **不给 `Quote` 加字段**，否则所有已批准草案的哈希都会改变 |
| `service.py` `Workflow.references_match / check_fresh / approve / export` | 依赖变化使草案失效；重复导出返回原文件 | 行为不变，只做 §5.4 的可选扩展 |
| `knowledge.py` `Knowledge.resolve(inputs, actor)` | 权限优先的受控参考选择，返回 `(ref, ref_hash, basis)` | 选择逻辑不变，M9 只增加交叉核对 |
| `main.py` `GET /runs/{id}/integration-preview` | 返回 `IntegrationPreview`，`mode="SIMULATED"` | 保持接口兼容，内部改为读假系统 |
| `tests/test_m6.py` | `test_integration_preview_is_read_only_and_hides_stale_erp`、`test_integration_boundary_and_revision_conflict`、`test_retry_and_replay_cannot_duplicate_approval_or_export` | 必须继续通过 |
| 数值 | DEV-001 计价结果 EUR 2,547.00 | 所有既有报价数值不得变化 |
| M7 评测 | 本地口径 positive 4/4、expected_block 8/8、`unsafe_negative_quotes=0`、faults 6/6 | 不得退化 |

注意：`Knowledge.visible()` 只对 `demo-engineer`、`demo-approver` 两个角色可见。后台派发做发送时重检，必须使用**发起同步的审批人身份**调用 `references_match`；审计里的执行者仍记为 `system-dispatcher`。

## 3. 不变量

- **I1 读有出处**：每次外部读取落成不可变 `SourceSnapshot`。
- **I2 先记后发**：出站意图先在本地事务持久化，外部调用在事务之外进行。
- **I3 结果未知先查后发**：超时或崩溃后，只允许查询对账，不允许直接重发。
- **I4 发送时重检**：发送前再次确认批准有效、`quote_hash` 匹配、快照未过期且来源版本未变、目标版本未变。任何一项失败都不发送。
- **I5 写后回读对账**：回读目标记录并逐字段比对，只有 `MATCH` 才能进入 `VERIFIED`。
- **I6 精度语义显式**：金额用 Decimal 字符串，币种和计量单位显式，舍入规则固定且带 `mapping_version`，映射失败一律阻断。
- **I7 失败分三类**：可重试、不可重试、结果未知，各自有预算和去向；预算耗尽进入隔离区。
- **I8 最小权限**：只写"草案"对象；PLM 与 MES 永远只读；模型没有任何写权限；出站载荷只能由已批准的报价快照经映射层生成，没有任何接口接受自由载荷。

## 4. 数据模型

### 4.1 Pydantic 契约（新增，建议放 `integrations/contracts.py`，沿用 `Contract` 基类）

```python
class ExternalRef(Contract):
    system: Literal["PLM", "ERP", "MES"]
    entity: str            # item_revision / cost_reference / quote_draft / work_order_feedback
    external_id: str
    plant: str

class SourceSnapshot(Contract):
    id: str
    run_id: str
    ref: ExternalRef
    source_version: str          # etag 或 revision
    as_of: datetime              # 来源声明的数据时点
    fetched_at: datetime         # 本系统读取时刻
    ttl_seconds: int = Field(gt=0)
    payload: dict                # 规范化后的载荷
    payload_sha256: str
    adapter_version: str
    mapping_version: str
    unknown_fields: list[str] = Field(default_factory=list)

class IntentState(StrEnum):
    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    RETRY_WAIT = "RETRY_WAIT"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    ACKED = "ACKED"
    VERIFIED = "VERIFIED"          # 终态
    DIVERGED = "DIVERGED"          # 需人工决定
    HELD = "HELD"                  # 发送前重检未通过，未外发
    REJECTED = "REJECTED"          # 终态，载荷被拒
    QUARANTINED = "QUARANTINED"    # 终态，预算耗尽或权限问题
    SUPERSEDED = "SUPERSEDED"      # 终态，批准已失效

class Receipt(Contract):
    external_id: str
    target_version: str
    acked_at: datetime
    response_sha256: str

class FieldDiff(Contract):
    field: str
    expected: str | None
    actual: str | None

class ReconciliationResult(Contract):
    status: Literal["MATCH", "DIFF", "MISSING", "DUPLICATE"]
    diffs: list[FieldDiff] = Field(default_factory=list)
    target_version: str | None = None
    checked_at: datetime

class OutboundIntent(Contract):
    id: str
    run_id: str
    target: Literal["ERP"]
    operation: Literal["CREATE_DRAFT", "UPDATE_DRAFT"]
    business_key: str                  # f"{run_id}:{target}"
    idempotency_key: str
    payload: dict
    payload_sha256: str
    approved_quote_hash: str
    base_snapshot_ids: list[str]
    mapping_version: str
    external_id: str | None = None
    expected_target_version: str | None = None
    state: IntentState
    attempts: int = 0
    max_attempts: int
    next_attempt_at: datetime | None = None
    lease_until: datetime | None = None
    lease_token: str | None = None
    last_error_class: str | None = None
    last_error_code: str | None = None
    receipt: Receipt | None = None
    requested_by: str
    created_at: datetime
    updated_at: datetime
```

新增契约要加入 `scripts/export_schemas.py` 的导出范围。

### 4.2 SQLite（`Repository` 升级到 `user_version=2`）

现有构造函数在 `version not in (0, 1)` 时抛错。改为**显式迁移**：`0/1 → 2` 只新增表和索引，不改动 `runs`、`events`。保持旧库可直接打开。

```sql
CREATE TABLE IF NOT EXISTS source_snapshots (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  system TEXT NOT NULL, entity TEXT NOT NULL, external_id TEXT NOT NULL, plant TEXT NOT NULL,
  source_version TEXT NOT NULL, as_of TEXT NOT NULL, fetched_at TEXT NOT NULL,
  ttl_seconds INTEGER NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  adapter_version TEXT NOT NULL, mapping_version TEXT NOT NULL,
  unknown_fields TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_snapshots_run ON source_snapshots(run_id);

CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  target TEXT NOT NULL, operation TEXT NOT NULL, business_key TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  approved_quote_hash TEXT NOT NULL, base_snapshot_ids TEXT NOT NULL, mapping_version TEXT NOT NULL,
  external_id TEXT, expected_target_version TEXT,
  state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
  next_attempt_at TEXT, lease_until TEXT, lease_token TEXT,
  last_error_class TEXT, last_error_code TEXT, receipt TEXT,
  requested_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- 同一 (run, target) 同时最多一条未终结的意图
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_active ON outbox(run_id, target)
  WHERE state NOT IN ('VERIFIED','REJECTED','QUARANTINED','SUPERSEDED');

CREATE TABLE IF NOT EXISTS outbox_attempts (
  intent_id TEXT NOT NULL REFERENCES outbox(id), attempt INTEGER NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT, error_class TEXT, error_code TEXT,
  http_status INTEGER, request_sha256 TEXT, response_sha256 TEXT, elapsed_ms INTEGER,
  PRIMARY KEY (intent_id, attempt)
);

CREATE TABLE IF NOT EXISTS reconciliations (
  id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES outbox(id),
  checked_at TEXT NOT NULL, status TEXT NOT NULL, diffs TEXT NOT NULL, target_version TEXT
);

CREATE TABLE IF NOT EXISTS idempotency_records (
  scope TEXT NOT NULL, key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
  status INTEGER, response TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  PRIMARY KEY (scope, key)
);
```

### 4.3 `Repository` 扩展

- 新增 `update_tx(run_id, expected_version, actor, action, change)`，其中 `change(body, conn)` 可拿到同一个连接，用于在**同一事务**里写 `outbox`。现有 `update` 改为对它的薄封装，签名不变。
- 新增 `append_event(conn, run_id, actor, action, detail)`：只追加审计事件，**不增加 run 的 `version`**。出站状态迁移用它记录，避免派发过程不断打断人工操作的版本号。
- 只有 `sync_requested` 这类用户动作才使用 `update_tx` 并提升 run 版本。

### 4.4 其他需要新增的基础设施

- `clock.py`：`Clock` 协议、`SystemClock`、`FakeClock`。派发器、对账器、快照服务都通过依赖注入使用它。退避抖动的随机数也可注入。
- `Settings` 新增（带校验，风格与现有一致）：`sync_max_attempts=5`、`sync_lease_seconds=60`、`snapshot_ttl_seconds=900`、`fake_systems_path`（默认放在 `data_dir` 下，独立于 `workbench.sqlite3`）。

## 5. 读路径：快照

### 5.1 端口

```python
class PlmPort(Protocol):
    def read_item_revision(self, part_number: str, plant: str) -> RawRecord: ...
    def probe_version(self, ref: ExternalRef) -> str: ...        # 仅取 etag

class ErpPort(Protocol):
    def read_cost_reference(self, material: str, plant: str) -> RawRecord: ...
    def probe_version(self, ref: ExternalRef) -> str: ...
    def find_by_idempotency_key(self, key: str) -> RawRecord | None: ...
    def find_by_business_key(self, business_key: str) -> list[RawRecord]: ...
    def read_draft(self, external_id: str) -> RawRecord: ...
    def put_draft(self, business_key: str, payload: dict, idempotency_key: str,
                  if_match: str | None) -> RawRecord: ...
    lookup_consistency: Literal["strong", "eventual"]
    settle_seconds: float

class MesPort(Protocol):
    def read_feedback(self, ...) -> RawRecord: ...
```

端口只抛两类异常：`TransportError(kind="connect" | "read_timeout" | "reset")` 与 `HttpError(status, body)`。适配器和重试逻辑只依赖这两类异常，便于将来换成真实 HTTP 客户端。

### 5.2 `SnapshotService`

流程：读取 → 容错校验 → 规范化 → 落库为 `SourceSnapshot`。

- **PLM**：以"发布状态 + 生效期"判断可用版次，不使用最大修订号。图纸版次与 PLM 发布版次不一致时沿用现有 `REVISION_CONFLICT` 逻辑。
- **ERP 成本参考**：必须有 `valid_from/valid_until`、币种、计量单位；校验规则复用 `costing.load_reference` 的思路。
- **容错读取**：关键字段缺失即阻断（`SNAPSHOT_SCHEMA_DRIFT`）；出现未知新增字段只记入 `unknown_fields` 并告警。这与内部契约的 `extra="forbid"` 是刻意不同的。
- **跨源一致性**：图纸零件号、PLM 零件号、ERP 物料主数据必须一致，否则 `SOURCE_CONFLICT` 并进入工程澄清。

### 5.3 与现有计价的衔接（降低改动面）

- **不替换** `Knowledge.resolve` 的选择逻辑，也不改变 `Quote.reference_hash` 的含义。
- 快照作为**前置校验与交叉核对**：ERP 成本参考快照的规范化载荷必须与 `Knowledge.resolve` 得到的 `ref` 在费率、币种、单位、有效期上一致，不一致返回 `SOURCE_CONFLICT`。假 ERP 用 `fixtures/reference/manufacturing.json` 播种，所以既有数值不变。
- 关键要求：**快照读取必须发生在 `repo.update` 事务之外**。`Workflow.cost` 改为：①事务外 `SnapshotService.acquire(...)`；②事务内只做纯校验（快照存在、未过期、与 `ref` 一致），并把 `snapshot_refs = {ids, set_sha256}` 写入 run body 和 `costed` 审计详情。`snapshot_refs` 不参与 `input_hash`，也不进入 `Quote`。
- 在 ADR 中记录演进路径：真实系统接入后，ERP 快照成为权威来源，本地 `reference` 降为缓存。

### 5.4 失效检查扩展

`Workflow.references_match(body, actor, *, probe=False)`：

- 默认 `probe=False`，行为与现在完全一致（纯本地，不做外部 I/O）。
- `probe=True` 时额外检查 `snapshot_refs`：每个快照未过 TTL，且 `probe_version` 返回的来源版本与快照一致。探测失败按**无法确认**处理：`approve` 与发送前重检场景失败关闭（`SNAPSHOT_UNAVAILABLE`），只读场景（下载、预览）不使草案失效。
- `approve`、`request_sync`、派发前重检使用 `probe=True`。

## 6. 写路径：出站意图与派发

### 6.1 状态机

```
PREPARED ──claim──▶ DISPATCHING ──成功──▶ ACKED ──回读 MATCH──▶ VERIFIED
                        │                    └─DIFF/MISSING/DUPLICATE─▶ DIVERGED
                        ├─可能已发出（读超时/连接重置/5xx）──▶ UNKNOWN_OUTCOME ──按键查询──▶ ACKED | PREPARED | DIVERGED
                        ├─确定未发出（连接失败/429）────────▶ RETRY_WAIT ──到期──▶ DISPATCHING
                        ├─409/412 版本冲突──────────────────▶ DIVERGED（不覆盖）
                        ├─400/422 载荷被拒──────────────────▶ REJECTED
                        └─401/403 或重试预算耗尽────────────▶ QUARANTINED
发送前重检失败 ─▶ HELD（再次重检仍失败且批准已失效 ─▶ SUPERSEDED）
```

### 6.2 创建意图：`request_sync`

由审批人触发，`Idempotency-Key` 必填。前置条件：任务状态为 `APPROVED_DRAFT` 或 `EXPORTED_DRAFT`，`references_match(probe=True)` 通过，`approval.quote_hash == fingerprint(quote)`。

- 用映射层**立即生成载荷**（失败则当场报错，不创建意图）。
- 操作类型：该 `(run, target)` 存在 `VERIFIED` 的上一条意图 → `UPDATE_DRAFT`，携带其 `external_id` 与回执里的 `target_version` 作为 `expected_target_version`；否则 `CREATE_DRAFT`。这样同一任务的新草案版本是**更新同一条外部草案**，不会新建。
- 幂等键：`sha256(canonical_json({run_id, target, operation, approved_quote_hash, mapping_version}))`。
- 在 `update_tx` 中同时写 `outbox` 行和 `sync_requested` 审计事件。部分唯一索引保证同一任务不会同时有两条活动意图（返回 `SYNC_ALREADY_ACTIVE`，409）。
- 如果已有相同批准哈希的 `VERIFIED` 意图，直接返回它（幂等）。

### 6.3 派发：`dispatch_once(intent_id)`

```
tx1（BEGIN IMMEDIATE）：
  读取意图；若是 UNKNOWN_OUTCOME → 先走 reconcile_unknown（见 6.4），不直接发送
  发送前重检（任一失败 → HELD 或 SUPERSEDED，记审计，结束）：
    S1 run 仍处于已批准状态，approval.quote_hash == intent.approved_quote_hash == fingerprint(quote)
    S2 references_match(body, requested_by, probe=True) 为真
    S3 base_snapshot_ids 对应快照未过 TTL，来源版本与 probe 一致
    S4 UPDATE 场景下：目标当前版本 == expected_target_version（否则 DIVERGED）
    S5 状态为 PREPARED，或 RETRY_WAIT 且已到 next_attempt_at
  claim：state=DISPATCHING，attempts+=1，lease_until=now+lease，lease_token=新随机值，写 attempts 行
提交 tx1
事务外：调用 ErpPort.put_draft(business_key, payload, idempotency_key, if_match)
tx2：凭 lease_token 完成；token 不匹配 → 丢弃结果，attempts 行标记 LATE（对应 LEASE_LOST）
  按 6.5 分类结果迁移状态；成功则写 receipt 并立刻 verify（6.4）
```

租约过期而状态仍为 `DISPATCHING` 的意图，由 `recover` 视为 `UNKNOWN_OUTCOME`（对应现有 `FileWorkflow.recover` 的思路）。

### 6.4 对账

`verify(intent)`：`read_draft(external_id)` 读回，规范化后与出站载荷逐字段比较。金额按 Decimal 比较，币种、数量、行数及各行值都要核对，并检查回写的追踪字段 `x_run_id`、`x_quote_hash`、`x_idempotency_key`。结果写入 `reconciliations`。

- `MATCH` → `VERIFIED`。
- `DIFF`（含缺行等部分写入）、`MISSING`、`DUPLICATE` → `DIVERGED`。
- `DUPLICATE`：`find_by_business_key` 返回多条。只告警并转人工，**不自动删除**。

`reconcile_unknown(intent)`：

1. 依次 `find_by_idempotency_key(key)`、`find_by_business_key(business_key)`。
2. 命中且载荷哈希一致 → 写回执，转 `ACKED` 再 `verify`。
3. 命中但内容不同 → `DIVERGED`。
4. 未命中：端口 `lookup_consistency == "strong"` → 回到 `PREPARED`（保留 attempts）；`"eventual"` → 等满 `settle_seconds` 后再查一次仍未命中才回到 `PREPARED`，期间保持 `UNKNOWN_OUTCOME`。

`reconcile_sweep(now)`：处理所有 `UNKNOWN_OUTCOME`、`ACKED` 但未验证、以及到期的 `RETRY_WAIT`。对已 `VERIFIED` 的意图做漂移巡检：目标被改动时只追加 `target_drift_detected` 审计和一条 `DIFF` 对账记录，**不改意图状态**。

### 6.5 错误分类

| 端口异常 | 含义 | 分类 | 迁移 |
|---|---|---|---|
| `TransportError(connect)` | 请求未发出 | 可重试 | `RETRY_WAIT` |
| `TransportError(read_timeout / reset)` | 可能已发出 | 结果未知 | `UNKNOWN_OUTCOME` |
| `HttpError` 429 | 未处理 | 可重试 | `RETRY_WAIT`（尊重 `Retry-After`） |
| `HttpError` 500/502/503/504 | 默认视为可能已处理 | 结果未知 | `UNKNOWN_OUTCOME`（端口可声明 `not_processed_statuses` 来覆盖） |
| `HttpError` 409/412 | 版本冲突 | 不可重试 | `DIVERGED` |
| `HttpError` 400/422 | 载荷被拒 | 不可重试 | `REJECTED` |
| `HttpError` 401/403 | 凭证或权限 | 不可重试 | `QUARANTINED` |
| 其他异常 | 未知 | 结果未知 | `UNKNOWN_OUTCOME` |

退避为指数加抖动（时钟与随机数可注入），`attempts` 达到 `max_attempts` 转 `QUARANTINED`。

### 6.6 人工决议：`resolve`

对 `DIVERGED`、`QUARANTINED` 的意图，审批人可选择：

- `rebase`：重新读取最新快照并重新走计价与批准流程，旧意图转 `SUPERSEDED`；
- `abandon`：旧意图转 `SUPERSEDED`，不再处理。

决议必须填写 `note`，并写入审计。（P2：`cancel_draft` 补偿操作，取消已验证的外部草案。）

## 7. 映射层与数值精度

- 纯函数模块 `integrations/mappers.py`，版本号 `erp-draft-v1`：
  `map_quote_to_erp_draft_v1(quote, inputs, run_id, approval) -> ErpDraftPayload`。
- 载荷字段：`business_key`、`plant`、`part_number`、`revision`、`quantity`（整数）、`currency`、`unit_price`、`total_price`、`lines[]`（`code/description/unit_cost/batch_cost`）、计量单位，以及追踪字段 `x_run_id / x_quote_hash / x_idempotency_key / x_mapping_version / x_draft_version`。
- 金额一律 Decimal 字符串；舍入规则（位数与舍入模式）写在 `mapping_version` 对应的常量里，由黄金样例固定。
- **映射后校验**：行批成本合计与报价总成本的差异不得超过 0.01 EUR，否则 `MAPPING_PRECISION` 阻断。若该容差与现有数据冲突，在 ADR 中记录取值依据，不得静默放宽。
- 不支持的币种、单位、工厂：`MAPPING_UNSUPPORTED` 阻断。
- `canonical.py`：规范化 JSON 的规则要写成代码和测试：Decimal 一律字符串、`None` 保留、时间统一为 UTC ISO 8601、禁止浮点数进入哈希。`fingerprint()` 对既有输入的结果必须不变，规范化函数用于**出站载荷与快照**。

## 8. API

所有写类接口使用现有的 `X-Demo-Token` 与 `approver` 依赖。

| 接口 | 角色 | 说明 |
|---|---|---|
| `POST /runs/{run_id}/sync` | approver | 请求体 `{expected_version, target:"ERP", note}`；需要 `Idempotency-Key` |
| `GET /runs/{run_id}/sync` | 任意已认证角色 | 返回意图列表、最近一次对账、尝试摘要 |
| `POST /sync/{intent_id}/dispatch` | approver | 手动触发一次 `dispatch_once` |
| `POST /sync/{intent_id}/reconcile` | approver | 触发对账 |
| `POST /sync/{intent_id}/resolve` | approver | `{decision:"rebase"\|"abandon", note}` |
| `POST /sync/dispatch-due` | approver | 处理到期意图，带 `limit`，供演示与脚本使用 |
| `GET /runs/{run_id}/integration-preview` | 现有 | 保持兼容，内部改读假系统与快照，继续标注 `SIMULATED` |

- **不使用进程内后台线程**作为队列（你们的文档已有此判断）。提供 `scripts/sync_worker.py --once` 作为显式 worker，测试直接调用函数。可选的循环模式默认关闭。
- 自动处理的审计执行者记为 `system-dispatcher`，人工触发则在 `detail.triggered_by` 记录操作者。

### 8.1 API 层幂等键

- 覆盖：`POST /tasks`、`/runs/upload`、`/runs/confirmed`、`/runs/from-fixture/{id}`、`/runs/{id}/approve`、`/runs/{id}/export`、`/runs/{id}/sync`。`/runs/{id}/sync` 必须带键，其余接受可选键。
- 键格式 16–128 个字符，字母数字加 `-_`。作用域为 `(actor, method, 路径模板)`，保留 24 小时。
- **请求指纹**：对解析后的内容取 SHA-256（上传接口用文件字节和表单字段，不用原始 multipart 流）。
- 同键同请求 → 返回首次响应（同状态码）；同键不同请求 → `IDEMPOTENCY_KEY_REUSED`（422）；同键请求仍在处理 → `IDEMPOTENCY_IN_PROGRESS`（409）。实现方式是先插入占位行，处理完成后回填。
- `expected_version` 保持不变。两者分工：版本号防并发修改，幂等键防重试。不带键的行为与现在完全一致。

## 9. 假系统与故障注入

`integrations/fakes.py` 提供**有状态**的 `FakePlm`、`FakeErp`、`FakeMes`，存储为独立的 SQLite 文件（模拟独立系统，也让网页演示跨进程可用）。测试可使用内存实现。

- `FakeErp.put_draft`：若 `idempotency_key` 已见过 → 返回首次回执（去重）；若该 `business_key` 已有记录，要求 `if_match` 等于当前 etag，否则 412；创建时 etag 为 `"1"`，每次更新递增。
- `FakeErp.read_cost_reference`：用 `fixtures/reference/manufacturing.json` 播种，字段包含有效期、币种、计量单位和 etag。
- `FakePlm`：`PM-BRKT-010` 发布版次为 B，并提供 `release_new_revision` 供测试模拟版次变更。
- `FakeMes`：沿用现有样例。
- **故障注入**：`FaultPlan` 以"端口方法 + 故障类型 + 次数"配置，类型包括 `timeout_before_commit`、`timeout_after_commit`、`duplicate_delivery`、`http_409`、`http_429`、`http_503`、`stale_read`、`partial_write`（只写头不写行）、`schema_drift`。保留现有 `RFQ_SIMULATE_MODEL_TIMEOUT` 的风格，通过环境变量或测试夹具开启。
- **崩溃注入**：派发器在固定位置调用 `failpoints.hit(name)`：`after_claim`、`after_send_before_record`、`after_record_before_verify`。测试让其抛出 `SimulatedCrash`，再用同一份 SQLite 与同一个假系统实例新建派发器，验证"重启后"的行为。

## 10. M9 独立评测

仿照 `evals/frozen/m7`：新增 `evals/frozen/m9/{protocol.json,scenarios.json}` 和 `scripts/evaluate_m9.py --output <dir>`。脚本使用临时数据目录和 `FakeClock`，输出结构与 M7 一致（`suite、mode、complete、protocol、cases、faults、summary`）。任何门槛不满足时以非零状态码退出，并加入 CI。验收时把结果写入 `evals/results/m9/local.json` 并提交（与 M7 的做法一致）。

场景（每个都断言**目标系统里的草案数量**与意图最终状态）：

| 编号 | 场景 | 期望 |
|---|---|---|
| M9-01 | 正常创建并验证 | 草案数 1，`VERIFIED` |
| M9-02 | 提交前超时 | `UNKNOWN_OUTCOME` → 查询未命中 → 重发，草案数 1 |
| M9-03 | **提交后超时** | `UNKNOWN_OUTCOME` → 查询命中 → `ACKED`，**不重发**，草案数 1 |
| M9-04 | 重复投递同一请求 | 目标去重，草案数 1 |
| M9-05 | 发送后崩溃，重启恢复 | 租约过期 → 对账，无丢失无重复 |
| M9-06 | 两个派发器并发 | 仅一个持有租约，草案数 1 |
| M9-07 | 迟到结果（租约已被回收） | 被丢弃，记录 `LATE`，状态不被覆盖 |
| M9-08 | 目标版本被他人修改（409/412） | `DIVERGED`，目标数据未被覆盖 |
| M9-09 | 429 与 503 | 退避重试；预算耗尽 → `QUARANTINED` |
| M9-10 | 排队期间 PLM 发布了新版次 | 发送前重检 → `HELD`，没有外发 |
| M9-11 | 排队期间批准失效（修改字段或重新计价） | `HELD` → `SUPERSEDED`，没有外发 |
| M9-12 | 部分写入（缺行） | 对账 `DIFF`，不得 `VERIFIED` |
| M9-13 | 币种、单位、舍入不一致 | 映射阻断，没有外发 |
| M9-14 | 接口漂移：缺关键字段 | 阻断；仅多出新字段只告警 |
| M9-15 | 同一任务第二次批准（新草案版本） | 走 `UPDATE_DRAFT`，仍然只有一条外部草案 |
| M9-16 | 相同 `Idempotency-Key` 的重试请求 | 返回首次响应；改请求体则被拒 |
| M9-17 | 目标已出现重复草案 | `DUPLICATE`，转人工，不自动删除 |

**硬门槛**（任一不满足即失败）：

- `duplicate_effect = 0`（目标系统出现重复草案）
- `lost_write = 0`
- `false_ack = 0`（标为 `VERIFIED` 但目标数据与载荷不一致）
- `unsafe_send = 0`（批准失效或快照过期仍外发）
- `unknown_outcome_resolved = 100%`
- `injected_mismatch_detection_recall = 100%`
- M7 评测结果不退化

可选（P2）：`tests/test_sync_model_based.py`，用 hypothesis 生成"请求、派发、崩溃、恢复、重试、批准变更"的随机序列，检查上述不变量。若引入需同步更新 `pyproject.toml` 的 dev 依赖与 `requirements.lock`，保证 CI 里 `pip install -r requirements.lock` 仍可用。

## 11. 任务拆分与验收

| 任务 | 内容 | 主要文件 | 验收 |
|---|---|---|---|
| **T1 契约与存储** | §4 的契约、迁移到 `user_version=2`、`update_tx`/`append_event`、`clock.py`、`Settings` 项 | `integrations/contracts.py`、`repository.py`、`config.py`、`clock.py` | 旧 v1 库可迁移且数据不变（`tests/test_migration.py`）；全部既有测试通过 |
| **T2 规范化与哈希** | `canonical.py` 与黄金样例 | `canonical.py`、`tests/test_canonical.py` | 同一载荷哈希稳定；`fingerprint()` 对既有输入输出不变 |
| **T3 端口与假系统** | `integrations.py` 拆分为包：`ports.py`、`fakes.py`、`mappers.py`；`preview_integrations` 接口兼容；故障与崩溃注入 | `integrations/` | M6 三个集成测试通过；故障注入单测通过 |
| **T4 只读快照** | `SnapshotService`；`Workflow.cost` 事务外取快照并交叉核对；`references_match(probe=...)` | `integrations/snapshots.py`、`service.py` | 快照过期或来源版本变化触发 `STALE_DRAFT`；所有既有报价数值不变；无外部 I/O 进入 `repo.update` 回调（用测试断言） |
| **T5 出站与派发** | `sync.py`：`request_sync`、`dispatch_once`、`recover`、`reconcile_unknown`、`verify`、`reconcile_sweep`、`resolve`；相关接口；`scripts/sync_worker.py` | `sync.py`、`main.py` | M9-01 至 M9-15、M9-17 作为单元或集成测试通过 |
| **T6 API 幂等键** | §8.1 | `main.py`、`repository.py` | M9-16 通过；不带键的既有行为不变 |
| **T7 评测** | `evals/frozen/m9`、`evaluate_m9.py`、CI 步骤 | `scripts/`、`evals/`、`.github/workflows/ci.yml` | §10 全部门槛达标；本地与 CI 结果一致 |
| **T8 界面与文档** | `integration-preview.tsx` 展示每个目标的意图状态、尝试次数、回执、最近对账差异，以及请求同步、重试、对账、决议按钮；ADR 05；M9 验收文档；更新架构、接口说明和数据契约文档 | `apps/web/app/`、`docs/` | `npx prettier --check`、`npm run build` 通过；接口模式 diff 无差异 |

**时间不够时的取舍顺序**（从最先保留到最先舍弃）：T1 → T3 → T5 → T7 → T2 → T4 → T6 → T8。T4 的跨源一致性检查和 T8 的界面可降级为文档说明；T6 可只覆盖 `approve` 与 `sync`。

T8 动手前先阅读 `apps/web/app/integration-preview.tsx` 与 `page.tsx`，复用现有的请求与错误处理风格。

## 12. 约束与禁止事项

1. **外部 I/O 不得放进 `Repository.update` 或 `update_tx` 的 `change` 回调。**
2. 模型不得触达任何写端口。出站载荷只能由已批准报价经映射层生成。
3. 不修改 `Quote` 字段和 `fingerprint()` 对既有输入的结果。
4. 时钟、休眠、随机数全部可注入，测试不得依赖真实时间。
5. 不新增后台线程式"队列"。
6. 新错误码保持稳定、可展示、不泄露底层细节，建议包括：`SNAPSHOT_STALE`、`SNAPSHOT_UNAVAILABLE`、`SNAPSHOT_SCHEMA_DRIFT`、`SOURCE_CONFLICT`、`MAPPING_PRECISION`、`MAPPING_UNSUPPORTED`、`SYNC_NOT_APPROVED`、`SYNC_ALREADY_ACTIVE`、`SYNC_PRECONDITION_FAILED`、`TARGET_DIVERGED`、`LEASE_LOST`、`IDEMPOTENCY_KEY_REUSED`、`IDEMPOTENCY_IN_PROGRESS`。
7. 界面、文档、讲稿保持 `SIMULATED / NO EXTERNAL WRITE`，不得出现"已对接 ERP"的表述。
8. 现有用户可见文案以中文为主，保持一致。代码行宽 100，遵守 Ruff 配置。
9. 在 ADR 05 中记录这些待确认的假设：真实 ERP 是否支持幂等键、5xx 是否保证未处理、查询是强一致还是最终一致、ERP 是否会成为成本参考的权威来源、0.01 EUR 容差的依据。

## 13. 完成定义

- 全部既有测试与 M7 评测不退化，Ruff 检查和格式检查通过。
- §10 的 M9 硬门槛全部达标，CI 已包含 M9 评测。
- 新契约和接口的模式文件已重新生成并提交。
- 前端 `prettier` 检查与生产构建通过。
- ADR 05、M9 验收文档、架构、接口说明和数据契约文档已更新；方案答卷中对集成的表述仍是"模拟、有真实语义"，没有夸大。
