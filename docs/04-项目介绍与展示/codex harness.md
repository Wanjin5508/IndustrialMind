我已经把这个分支的后端代码、测试、评测和设计文档读了一遍。这是静态阅读：仓库只有一个提交 `3d28ab9`，我没有安装依赖，也没有跑测试。下面是分析和可以交给 Codex 的优化方案。

## 一、现状：已经很扎实的部分

下面这些设计是对的，Codex 改动时不要破坏：

- **证据链**：来源 ID 由解析器签发，`verify()` 逐字段核对文本，模型无法伪造引用。
- **并发控制**：任务用 `expected_version` 做乐观锁。
- **审批绑定**：批准绑定 `fingerprint(quote)`，依赖变化时用 `check_fresh` 使草案失效。
- **抽取容错**：`analyze` 采用 claim、调用、complete 三段式，带租约、重试预算和迟到结果拒收。
- **评测与 CI**：M7 冻结评测有 `unsafe_negative_quotes=0` 这类硬指标，CI 会重新生成接口模式并做 diff。

## 二、与"跨系统读写"相比的缺口

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| 1 | `integrations.py` | `SimulatedERP.preview` 是用本任务的 quote 反推出来的，PLM 的 `released_revision` 写死为 `"B"` | 外部数据永远和本地一致，对账和漂移检测无从验证 |
| 2 | `costing.load_reference` | `reference_hash` 是本地 JSON 文件的哈希，没有来源版本、`as_of`、`fetched_at` | 无法知道读到的是哪一刻的哪个版本，发送前也没法判断是否过期 |
| 3 | `service.export` | 只生成 Markdown/JSON，没有出站意图、回执和对账 | 没有写路径 |
| 4 | `main.py` 的创建类接口 | `POST /tasks`、`/runs/upload`、`/runs/confirmed` 没有幂等键 | 重试会产生重复任务 |
| 5 | `approve` | 响应丢失后重试，会被 `quote_snapshot` 判成 `VERSION_CONFLICT` 409 | 客户端分不清"第一次已成功"和"别人改了任务"（已有测试证明不会重复审批，但语义含糊） |
| 6 | `Repository.update` | `change` 回调在 `BEGIN IMMEDIATE` 事务内执行 | **外部调用不能放进去**：长事务，外部副作用也无法回滚 |
| 7 | `Repository.__init__` | `user_version` 不在 (0,1) 就抛错；任务 body 是整份 JSON | 需要迁移机制；尝试日志塞进 body 会膨胀 |
| 8 | `Quote` 与 `fingerprint` | `Quote` 是 `extra="forbid"`，且 `approval.quote_hash` 依赖它的哈希 | 给 `Quote` 加字段会让所有已批准草案失效，新数据必须放在 `Quote` 之外 |

## 三、总体思路：两道闸

1. **模型闸**（已完成）：模型只做抽取，输出经证据校验。
2. **系统闸**（本次新增）：任何进出外部系统的数据，都经过确定性代码，带来源凭证、幂等键和对账。**模型没有任何写权限。**

## 四、集成 harness 的核心设计

### 4.1 不变量（所有实现必须满足）

- **I1 读有出处**：每次外部读取都落成不可变的 `SourceSnapshot`，计价和审批只依赖快照。
- **I2 写先记后发**：先在本地事务里持久化"出站意图"，再在事务之外调用外部系统。
- **I3 结果未知时只查不发**：超时或崩溃后，先按幂等键或业务键向目标系统查询，再决定是否重发。
- **I4 发送时重检**：发送前再检查批准是否有效、`quote_hash` 是否匹配、快照是否过期、目标版本是否变化，任何一项失败都不发送。
- **I5 写后回读**：回读目标系统并逐字段对账，通过才标记 `VERIFIED`。
- **I6 精度与语义显式**：金额用 Decimal 字符串，币种、单位、舍入规则固定且带版本，映射失败一律阻断（fail-closed）。
- **I7 失败分三类**：可重试、不可重试、结果未知，各有预算和去向，耗尽后进入隔离区，由人工补偿。
- **I8 最小权限**：读写凭证分离；只能写"草案"对象；PLM 永远只读。

### 4.2 读：`SourceSnapshot`

```
ExternalRef:     system(PLM|ERP|MES), entity, external_id, plant
SourceSnapshot:  id, ref, source_version(etag/revision), as_of, fetched_at, ttl_seconds,
                 payload(规范化后), payload_sha256, adapter_version, mapping_version
```

- **PLM**：以"发布状态加生效期"判断可用版次，不用最大修订号。图纸版次与 PLM 发布版次不一致，沿用现有的 `REVISION_CONFLICT`。
- **ERP**：费率和物料必须带 `valid_from/valid_until`、币种和计量单位。校验逻辑复用 `load_reference`，但数据来自快照。
- **跨源一致性**：PLM 零件号、ERP 物料主数据、图纸零件号三者必须一致，否则进入工程澄清。
- **失效统一**：`references_match` 扩展为同时比较快照的 `source_version` 和 TTL，与现有 `STALE_DRAFT` 机制合并。`reference_hash` 改为快照集合的哈希。
- **容错读取**：对外部载荷"关键字段缺失即阻断，新增字段只告警"。这与内部契约的 `extra="forbid"` 刻意不同，目的是识别接口漂移。

### 4.3 写：Outbox 状态机

```
PREPARED → DISPATCHING → ACKED → VERIFIED
DISPATCHING --超时/崩溃--> UNKNOWN_OUTCOME --按键查询--> ACKED | PREPARED(确认未写入) | DIVERGED
DISPATCHING --409 版本冲突--> DIVERGED（不覆盖，转人工）
DISPATCHING --4xx 校验错--> REJECTED（终态）
DISPATCHING --429/5xx--> RETRY_WAIT → DISPATCHING（预算耗尽 → 隔离区）
发送前重检失败 → HOLD（不发送）
```

- **出站表**：`outbox` 存意图，包括 `run_id`、目标系统、操作、`idempotency_key`、规范化载荷及其哈希、`approved_quote_hash`、依赖的快照 ID、`expected_target_version`、状态、租约。
- **事务性发件箱**：创建意图与审计事件在同一个事务里。
- **派发流程**：沿用 `analyze` 的模式，先 claim（置 `DISPATCHING`、`attempt+1`、写租约），再在事务外调用，最后在新事务里记结果。
- **崩溃恢复**：租约过期按 `UNKNOWN_OUTCOME` 处理，先查询对账，再决定是否重发。
- **幂等键**：`sha256(run_id | 目标系统 | 操作 | approved_quote_hash | mapping_version)`。同一份批准草案永远得到同一个键。
- **唯一性**：`(run_id, 目标系统, 操作类型)` 唯一，新草案版本对应对外部草案的**更新**，而不是新建。
- **目标系统不支持幂等键时**：把键写进自定义字段，写前先按键查询（find-before-create）。

### 4.4 对账与映射

- **对账结果**：`MATCH | DIFF | MISSING | DUPLICATE`，附字段级差异。只有 `MATCH` 才能把意图推进到 `VERIFIED`。
- **重复检测**：按业务键查询到多条时只告警，不自动删除。
- **映射层**：放在独立的纯函数模块，带 `mapping_version`。要有金额、币种、单位、舍入的黄金样例测试，以及映射后再反向映射的往返测试。

### 4.5 API 层幂等

- 在创建类接口以及 `approve`、`export`、`sync` 上增加 `Idempotency-Key` 请求头。
- 新表 `idempotency_records(key, scope, request_sha256, response, created_at)`。
- 同键同请求返回原响应；同键不同请求返回 `IDEMPOTENCY_KEY_REUSED`（422）。
- `expected_version` 保留，两者分工不同：版本号防并发修改，幂等键防重试。

### 4.5 补充：权限

- 同步请求由 `demo-approver` 触发，分发和对账由系统执行者 `system-dispatcher` 记入审计。
- 载荷只能由"已批准的 quote 快照"经映射层生成，没有任何接口可以传入自由载荷。

## 五、模拟目标系统与故障注入

把 `Simulated*` 换成**有状态的假系统**：自带记录存储、版本号（etag）、幂等键去重表。假系统要体现真实语义，而不是返回写死的数据。故障通过注入器触发（沿用你现有的 `RFQ_SIMULATE_MODEL_TIMEOUT` 风格）。

| 故障 | 期望行为 | 关键断言 |
|---|---|---|
| 提交前超时 | `UNKNOWN` → 查询未命中 → 重发 | 目标草案数为 1 |
| **提交后超时**（最危险） | `UNKNOWN` → 查询命中 → 记为 `ACKED`，**不重发** | 草案数为 1 |
| 重复投递 | 目标按键去重 | 草案数为 1 |
| 发送后崩溃 | 租约过期 → 对账 | 无丢失、无重复 |
| 并发派发 | 只有一个持有租约 | 草案数为 1 |
| 目标版本被改（409） | `DIVERGED`，不覆盖 | 目标数据未被改写 |
| 429/503 | 退避重试，预算耗尽进隔离区 | 状态正确 |
| 读到发送之间 PLM 版次变更 | 发送时重检 → `HOLD` | 没有外发 |
| 排队期间批准失效 | `HOLD` | 没有外发 |
| 部分写入（缺行） | 回读 `DIFF` | 不得为 `VERIFIED` |
| 币种、单位、舍入不一致 | 映射阻断 | 没有外发 |
| 接口漂移（缺关键字段） | 阻断；新增字段告警 | 状态正确 |

## 六、评测与门槛

仿照 `evals/frozen/m7` 增加 `evals/frozen/m9` 和 `scripts/evaluate_m9.py`，加入 CI。硬指标：

- `duplicate_effect = 0`（目标系统出现重复草案）
- `lost_write = 0`
- `false_ack = 0`（标为 `VERIFIED` 但目标数据不一致）
- `unsafe_send = 0`（批准失效或快照过期仍然外发）
- `unknown_outcome_resolved = 100%`
- `injected_mismatch_detection_recall = 100%`

可选增加一个基于模型的随机测试（用 hypothesis 生成"派发、崩溃、恢复、重试、批准变更"的随机序列，检查上述不变量），用来证明你不只是写了固定用例。

## 七、给 Codex 的任务拆分（按顺序，每项可独立合并）

| 任务             | 内容                                                                                                                                                        | 主要文件                                 | 验收                        |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ | ------------------------- |
| **T1 契约与存储**   | `ExternalRef`、`SourceSnapshot`、`OutboundIntent` 等契约；schema v2 迁移，新增 `source_snapshots`、`outbox`、`outbox_attempts`、`reconciliations`、`idempotency_records` | `contracts.py`（或新模块）、`repository.py` | v1 数据库可迁移；旧测试全部通过         |
| **T2 规范化与哈希**  | `canonical.py`：Decimal、时间、None 的规范化规则                                                                                                                     | 新文件                                  | 黄金样例，同载荷哈希稳定              |
| **T3 端口与假系统**  | `integrations/` 拆成 `ports.py`、`fakes.py`、`mappers.py`；`preview_integrations` 接口保持兼容                                                                       | `integrations.py`                    | 故障注入可用；现有 M6 测试通过         |
| **T4 只读快照**    | 读取、校验、落库；成本计算与 `references_match` 改用快照                                                                                                                    | `costing.py`、`service.py`            | 快照过期或版本变化触发 `STALE_DRAFT` |
| **T5 出站与派发**   | `sync.py`：请求同步、`dispatch_once`、恢复、对账；新增 `POST /runs/{id}/sync`、`POST /sync/{id}/reconcile`、`GET /runs/{id}/sync`                                          | 新文件、`main.py`                        | 第五节故障矩阵全部通过               |
| **T6 API 幂等键** | `Idempotency-Key` 依赖与存储                                                                                                                                   | `main.py`                            | 重试返回原响应；键复用被拒             |
| **T7 评测**      | `evals/frozen/m9`、`evaluate_m9.py`、CI 步骤                                                                                                                  | `scripts/`、`ci.yml`                  | 第六节指标全部达标                 |
| **T8 界面与文档**   | `integration-preview.tsx` 展示意图状态、回执、差异、重试与补偿；新增 ADR 005；重新生成接口模式                                                                                          | `apps/web`、`docs`                    | CI 的 schema diff 通过       |

**时间不够时的取舍**：优先保留 T1、T3、T5、T7 的最小版本（意图表、假系统、派发、评测）；T6 可简化为只覆盖 `approve` 和 `sync`；T4 的跨源一致性检查和 T8 的界面可以降级为文档说明。

## 八、约束（请原样写进给 Codex 的指令）

1. **外部调用绝不放进 `Repository.update` 的事务回调里。**
2. 模型没有任何写权限，载荷只能来自已批准快照。
3. **不修改 `Quote` 字段**，新数据放在它之外，否则已有批准的哈希会失效。
4. 时钟、休眠、随机数可注入，保证测试可重复。
5. 保持 `SIMULATED / NO EXTERNAL WRITE` 标注，文档和讲稿里不能写"已对接真实 ERP"。你的路线图已经强调了"超时后先查询再重试，不能假定没有返回就是没有写入"，这次是把它落成代码。
6. 遵守 Ruff（行宽 100）和现有 CI，改动接口后重新运行 `export_schemas.py` 与 `export_openapi.py`。

## 面试时可以怎么讲

核心一句话：**"模型只能读和建议，所有跨系统写入都经过'先记后发、发送时重检、结果未知先查后发、写后回读对账'这条确定性管道，并用故障注入评测证明重复写和丢写为零。"** 这比"我接了 ERP"更能说明你理解企业集成的难点。

如果你想让我把上面的内容整理成一份可以直接复制给 Codex 的 Markdown 任务文档，告诉我，我来生成。