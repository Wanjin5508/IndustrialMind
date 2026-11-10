"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import KnowledgePanel, { type KnowledgeItem } from "./knowledge-panel";
import { CreateTask, TaskControls, type TaskContext } from "./task-controls";
import IntegrationPreview from "./integration-preview";

type Evidence = {
  id: string;
  document_id: string;
  page: number;
  text: string;
  locator: string;
};
type Fact = {
  value: string | null;
  source_id: string | null;
  original_value: string | null;
  human_confirmed: boolean;
  evidence?: {
    status: string;
    source_exists: boolean;
    value_supported: boolean;
    locator: string | null;
    message: string;
  };
  correction?: { reason: string };
};
type Quote = {
  knowledge_basis?: KnowledgeItem[];
  routing?: { step: number; operation: string; basis: string }[];
  draft_version: number;
  quantity: number;
  unit_cost: string;
  unit_price: string;
  total_price: string;
  margin: string;
  rate_version: string;
  template_version: string;
  reference_hash: string;
  lines: {
    code: string;
    description: string;
    unit_cost: string;
    basis: string;
  }[];
  pending: string[];
};
type Run = {
  orchestration?: TaskContext;
  id: string;
  title: string;
  version: number;
  state: string;
  mode: string;
  extraction_attempt?: { number: number; limit?: number };
  inputs: object | null;
  fields: Record<string, Fact>;
  sources: Evidence[];
  documents: { id: string; name: string; kind: string; suffix: string }[];
  issues: { code: string; field: string; message: string }[];
  quote: Quote | null;
  approval: { actor: string; note: string } | null;
  export: { filename: string; markdown: string } | null;
  events?: {
    sequence: number;
    occurred_at: string;
    actor: string;
    action: string;
    detail: unknown;
  }[];
  metrics: {
    real_model_call?: boolean;
    attempted_live?: boolean;
    model?: string;
    endpoint_host?: string;
    elapsed_ms?: number;
    response_id?: string;
    replay_source?: { id: string; model: string; recorded_on: string };
    error_code?: string;
    estimated_cost?: string | null;
    cost_currency?: string;
    cost_note?: string;
    usage?: { input_tokens: number; output_tokens: number };
    pricing?: { version: string; source: string };
  };
};
type Capability = {
  live_available: boolean;
  model: string | null;
  simulate_model_timeout?: boolean;
};
const views = ["任务总览", "资料与证据", "成本草案", "审批与审计", "受控知识"];
const evidenceNames: Record<string, string> = {
  MISSING: "未知 · 需补充",
  INVALID_SOURCE: "无效来源",
  TEXT_VERIFIED: "文本依据已核对",
  TEXT_MISMATCH: "原文不支持该值",
  AMBIGUOUS: "同文档存在冲突",
  VISUAL_REVIEW: "待人工核对",
};
const names: Record<string, string> = {
  "rfq.part_number": "RFQ 零件号",
  "rfq.required_revision": "要求版次",
  "rfq.quantity": "数量",
  "rfq.currency": "币种",
  "drawing.part_number": "图纸零件号",
  "drawing.revision": "图纸版次",
  "drawing.material": "材料",
  "drawing.unit": "单位",
  "drawing.width_mm": "宽度",
  "drawing.height_mm": "高度",
  "drawing.thickness_mm": "厚度",
  "drawing.hole_diameter_mm": "孔径",
  "drawing.surface_finish": "表面处理",
};
const modeNames: Record<string, string> = {
  LOCAL_TEXT: "本地文本识别",
  LIVE: "在线图文识别",
  REPLAY: "回放（REPLAY）",
  FILES: "待识别",
  TASK: "待处理",
  MANUAL_CONFIRMED: "人工录入",
};
const states: Record<string, string> = {
  CLARIFICATION_REQUIRED: "待澄清 / 补件",
  REVIEW_COMPLETED: "图纸审查完成",
  KNOWLEDGE_COMPLETED: "知识查询完成",
  RECEIVED: "待抽取",
  VALIDATING: "可计算",
  NEEDS_ENGINEERING_REVIEW: "待工程复核",
  READY_FOR_APPROVAL: "待审批",
  APPROVED_DRAFT: "已批准内部草案",
  EXPORTED_DRAFT: "已导出",
  FAILED_RETRYABLE: "失败 · 可重试",
  FAILED_FINAL: "识别已停止",
  EXTRACTING: "抽取中",
};

export default function Workbench() {
  const [runs, setRuns] = useState<Run[]>([]),
    [run, setRun] = useState<Run | null>(null);
  const [view, setView] = useState(0),
    [token, setToken] = useState("demo-engineer");
  const activeRole = useRef(token);
  activeRole.current = token;
  const [fixtures, setFixtures] = useState<{ id: string; label: string }[]>([]);
  const [cap, setCap] = useState<Capability | null>(null),
    [mode, setMode] = useState("local_text");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [source, setSource] = useState<Evidence | null>(null),
    [preview, setPreview] = useState("");
  const [fieldKey, setFieldKey] = useState("rfq.quantity"),
    [value, setValue] = useState(""),
    [reason, setReason] = useState("");
  const [rfq, setRfq] = useState<File | null>(null),
    [drawing, setDrawing] = useState<File | null>(null);
  const [replacement, setReplacement] = useState<File | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [note, setNote] = useState("");

  const request = useCallback(
    async <T,>(
      path: string,
      method = "GET",
      body?: object | FormData,
    ): Promise<T> => {
      const response = await fetch(`/api${path}`, {
        method,
        headers: {
          "X-Demo-Token": token,
          ...(body && !(body instanceof FormData)
            ? { "Content-Type": "application/json" }
            : {}),
        },
        body: body
          ? body instanceof FormData
            ? body
            : JSON.stringify(body)
          : undefined,
      });
      const data = await response.json();
      if (!response.ok)
        throw new Error(
          data.error?.message || JSON.stringify(data.detail || "请求失败"),
        );
      return data as T;
    },
    [token],
  );
  const refresh = useCallback(async () => {
    const [items, catalog, capabilities] = await Promise.all([
      request<Run[]>("/runs"),
      request<{ id: string; label: string }[]>("/fixtures"),
      request<Capability>("/capabilities"),
    ]);
    // A response from the previous role must not restore its private task list.
    if (activeRole.current !== token) return;
    setRuns(items);
    setFixtures(catalog);
    setCap(capabilities);
  }, [request, token]);
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, [refresh]);
  useEffect(() => {
    setSource(null);
    setConfirmed(false);
  }, [run?.id, run?.version]);
  useEffect(() => {
    setMode(
      run?.mode === "LIVE"
        ? "live"
        : run?.mode === "REPLAY"
          ? "replay"
          : "local_text",
    );
  }, [run?.id, run?.mode]);
  useEffect(() => {
    let cancelled = false,
      url = "";
    setPreview("");
    if (
      source &&
      run?.documents.some(
        (d) => d.id === source.document_id && d.suffix === ".pdf",
      )
    ) {
      fetch(
        `/api/runs/${run.id}/documents/${source.document_id}/pages/${source.page}`,
        { headers: { "X-Demo-Token": token } },
      )
        .then((r) => {
          if (!r.ok) throw new Error("页图加载失败");
          return r.blob();
        })
        .then((blob) => {
          if (!cancelled) {
            url = URL.createObjectURL(blob);
            setPreview(url);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(e.message);
        });
    }
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [source, run?.id, run?.documents, token]);

  async function act(action: () => Promise<Run>, nextView?: number) {
    setBusy(true);
    setError("");
    try {
      const updated = await action();
      setRun(updated);
      await refresh();
      if (nextView !== undefined) setView(nextView);
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
      if (run) {
        try {
          setRun(await request<Run>(`/runs/${run.id}`));
        } catch {
          /* Preserve original action error. */
        }
      }
    } finally {
      setBusy(false);
    }
  }
  function mutate(endpoint: string, extra: object = {}) {
    return request<Run>(`/runs/${run!.id}/${endpoint}`, "POST", {
      expected_version: run!.version,
      ...extra,
    });
  }
  function download() {
    if (!run?.export) return;
    const url = URL.createObjectURL(
      new Blob([run.export.markdown], { type: "text/markdown;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = run.export.filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const engineer = token !== "demo-approver";
  const quoteTask =
    !run?.orchestration || run.orchestration.intent === "prepare_quote";
  const retryBlocked =
    run?.state === "FAILED_FINAL" && run.mode === mode.toUpperCase();
  const onlineLabel = cap?.simulate_model_timeout
    ? "在线识别 · 模拟超时"
    : "在线图文识别";
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="mark">PM</span>
          <div>
            PrecisionMotion<small>ENGINEERING WORKBENCH</small>
          </div>
        </div>
        <div className="workspace">德国工厂 / DE-DEMO</div>
        <nav>
          {views.map((label, i) => (
            <button
              key={label}
              className={view === i ? "active" : ""}
              onClick={() => setView(i)}
            >
              <span>0{i + 1}</span>
              {label}
            </button>
          ))}
        </nav>
        <div className="aside-bottom">
          <span className="dot" />
          演示环境<p>PM-BRKT-010 · EUR / mm</p>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            RFQ 工作台 <span>/</span> {views[view]}
          </div>
          <label className="role">
            当前角色{" "}
            <select
              aria-label="当前角色"
              value={token}
              disabled={busy}
              onChange={(e) => {
                const next = e.target.value;
                setToken(next);
                setRuns([]);
                if (
                  run?.orchestration &&
                  [null, "search_knowledge"].includes(
                    run.orchestration.intent,
                  ) &&
                  run.orchestration.owner !== next
                )
                  setRun(null);
              }}
            >
              <option value="demo-engineer">工程师</option>
              <option value="demo-approver">审批人</option>
            </select>
          </label>
        </header>
        <div className="content">
          <div className="heading">
            <div>
              <h1>{views[view]}</h1>
            </div>
          </div>
          {error && (
            <div role="alert" className="alert danger">
              {error}
              <button onClick={() => setError("")}>关闭</button>
            </div>
          )}
          {busy && (
            <div role="status" className="alert">
              正在处理，请稍候…
            </div>
          )}
          {view === 0 ? (
            <>
              <div className="stats">
                <article>
                  <small>任务总数</small>
                  <strong>{runs.length}</strong>
                </article>
                <article>
                  <small>需要工程复核</small>
                  <strong>
                    {
                      runs.filter((r) => r.state === "NEEDS_ENGINEERING_REVIEW")
                        .length
                    }
                  </strong>
                </article>
                <article>
                  <small>已批准 / 已导出</small>
                  <strong>{runs.filter((r) => r.approval).length}</strong>
                </article>
                <article>
                  <small>识别方式</small>
                  <strong className="text-stat">
                    {cap?.live_available ? "本地 / 在线" : "本地"}
                  </strong>
                </article>
              </div>
              <CreateTask
                key={token}
                engineer={engineer}
                busy={busy}
                fixtures={fixtures}
                create={(payload) =>
                  act(() => request<Run>("/tasks", "POST", payload), 1)
                }
              />
              <section>
                <div className="section-heading">
                  <h2>快捷打开 RFQ 资料</h2>
                  <span>样例 / 文件上传</span>
                </div>
                <div className="fixture-grid">
                  {fixtures.map((f) => (
                    <button
                      disabled={busy || !engineer}
                      key={f.id}
                      className="fixture"
                      onClick={() =>
                        act(
                          () =>
                            request<Run>("/tasks", "POST", {
                              intent: "prepare_quote",
                              fixture_id: f.id,
                            }),
                          1,
                        )
                      }
                    >
                      <small>{f.id}</small>
                      <strong>{f.label}</strong>
                      <span>打开资料 →</span>
                    </button>
                  ))}
                </div>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (!rfq || !drawing) return;
                    const data = new FormData();
                    data.append("rfq", rfq);
                    data.append("drawing", drawing);
                    act(() => request<Run>("/runs/upload", "POST", data), 1);
                  }}
                  className="upload"
                >
                  <label>
                    RFQ · UTF-8 文本或 PDF
                    <input
                      aria-label="RFQ 文件"
                      type="file"
                      accept=".md,.txt,.pdf"
                      onChange={(e) => setRfq(e.target.files?.[0] || null)}
                    />
                  </label>
                  <label>
                    图纸 · PDF
                    <input
                      aria-label="图纸文件"
                      type="file"
                      accept=".pdf"
                      onChange={(e) => setDrawing(e.target.files?.[0] || null)}
                    />
                  </label>
                  <button
                    className="primary"
                    disabled={!rfq || !drawing || busy || !engineer}
                  >
                    上传并创建
                  </button>
                </form>
                <p className="muted">单文件最大 5 MiB，PDF 最多 3 页。</p>
              </section>
              <section>
                <h2>最近任务</h2>
                {!runs.length ? (
                  <p className="empty">暂无任务。请选择样例或上传资料。</p>
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>任务 / 输入文件</th>
                          <th>状态</th>
                          <th>抽取模式</th>
                          <th>版本</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {runs.map((r) => (
                          <tr key={r.id}>
                            <td>
                              <strong>{r.title}</strong>
                              <small>{r.id.slice(0, 12)}</small>
                            </td>
                            <td>
                              <span className="badge">
                                {states[r.state] || r.state}
                              </span>
                            </td>
                            <td>{modeNames[r.mode] || r.mode}</td>
                            <td>v{r.version}</td>
                            <td>
                              <button
                                onClick={() =>
                                  act(() => request<Run>(`/runs/${r.id}`), 1)
                                }
                              >
                                查看 →
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          ) : view === 4 ? (
            <KnowledgePanel key={token} token={token} />
          ) : !run ? (
            <section className="empty">
              <h2>尚未选择任务</h2>
              <p>请先在任务总览中创建或选择一个 RFQ。</p>
              <button className="primary" onClick={() => setView(0)}>
                前往任务总览
              </button>
            </section>
          ) : (
            <>
              <div className="task-bar">
                <div>
                  <strong>{run.title}</strong>
                  <small>
                    {run.id.slice(0, 12)} · v{run.version}
                  </small>
                </div>
                <span className="badge">{states[run.state] || run.state}</span>
                <span className="muted">
                  {modeNames[run.mode] || run.mode}{" "}
                  {run.metrics.elapsed_ms !== undefined &&
                    `· ${run.metrics.elapsed_ms} ms`}
                </span>
                <button
                  disabled={busy}
                  onClick={() => act(() => request<Run>(`/runs/${run.id}`))}
                >
                  刷新
                </button>
                {engineer && run.state === "EXTRACTING" && (
                  <button
                    disabled={busy}
                    onClick={() => act(() => mutate("recover"))}
                  >
                    恢复中断任务
                  </button>
                )}
              </div>
              {run.orchestration && (
                <TaskControls
                  key={`${run.id}-${run.orchestration.intent}`}
                  task={run.orchestration}
                  version={run.version}
                  state={run.state}
                  engineer={engineer}
                  busy={busy}
                  live={!!cap?.live_available}
                  onlineLabel={onlineLabel}
                  retryBlocked={retryBlocked}
                  mode={mode}
                  setMode={setMode}
                  resume={(payload) =>
                    act(() =>
                      request<Run>(`/tasks/${run.id}/resume`, "POST", {
                        expected_version: run.version,
                        ...payload,
                      }),
                    )
                  }
                  attach={(data) =>
                    act(() =>
                      request<Run>(`/tasks/${run.id}/documents`, "POST", data),
                    )
                  }
                />
              )}
              {run.issues.length > 0 && (
                <section className="issues">
                  <h2>需要处理的工程问题 · {run.issues.length}</h2>
                  {run.extraction_attempt?.limit &&
                    ["FAILED_RETRYABLE", "FAILED_FINAL"].includes(
                      run.state,
                    ) && (
                      <p>
                        识别尝试 {run.extraction_attempt.number} /{" "}
                        {run.extraction_attempt.limit}
                      </p>
                    )}
                  {retryBlocked && <p>请选择其他识别方式或更新资料。</p>}
                  {run.issues.map((i, n) => (
                    <div key={n}>
                      <strong>{i.code}</strong>
                      <span>{names[i.field] || i.field}</span>
                      <p>{i.message}</p>
                    </div>
                  ))}
                </section>
              )}
              {view === 1 &&
                (!run.orchestration ||
                  ["prepare_quote", "review_drawing"].includes(
                    run.orchestration.intent || "",
                  )) && (
                  <>
                    <section className="toolbar">
                      <div>
                        <h2>文件抽取</h2>
                        <p className="muted">
                          {run.documents.map((d) => d.name).join(" + ")}
                        </p>
                      </div>
                      <select
                        aria-label="抽取模式"
                        value={mode}
                        onChange={(e) => setMode(e.target.value)}
                      >
                        <option value="local_text">本地文本识别</option>
                        <option value="live" disabled={!cap?.live_available}>
                          {onlineLabel}
                        </option>
                        <option value="replay">回放（REPLAY）</option>
                      </select>
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          !engineer ||
                          retryBlocked ||
                          run.state === "EXTRACTING"
                        }
                        onClick={() => act(() => mutate("analyze", { mode }))}
                      >
                        {run.state === "FAILED_RETRYABLE"
                          ? "重新识别"
                          : "开始抽取"}
                      </button>
                    </section>
                    {run.metrics.replay_source && (
                      <p className="muted">
                        回放来源：{run.metrics.replay_source.id} ·{" "}
                        {run.metrics.replay_source.model} ·{" "}
                        {run.metrics.replay_source.recorded_on}
                      </p>
                    )}
                    <div className="evidence-grid">
                      <section>
                        <h2>结构化字段</h2>
                        {!Object.keys(run.fields).length && (
                          <p className="empty">
                            点击开始抽取，查看字段及其原文依据。
                          </p>
                        )}
                        {Object.entries(run.fields)
                          .filter(
                            ([key]) =>
                              run.orchestration?.intent !== "review_drawing" ||
                              key.startsWith("drawing."),
                          )
                          .map(([key, fact]) => (
                            <div className="fact" key={key}>
                              <div>
                                <small>{names[key] || key}</small>
                                <strong>{fact.value ?? "未知 / 待补充"}</strong>
                                <small>
                                  {fact.evidence
                                    ? evidenceNames[fact.evidence.status] ||
                                      fact.evidence.status
                                    : "待核对"}
                                </small>
                                {fact.human_confirmed && (
                                  <small className="human">
                                    人工确认 · 原值{" "}
                                    {fact.original_value ?? "未知"}
                                  </small>
                                )}
                              </div>
                              <button
                                disabled={
                                  !fact.source_id ||
                                  (fact.evidence &&
                                    !fact.evidence.source_exists)
                                }
                                onClick={() =>
                                  setSource(
                                    run.sources.find(
                                      (s) => s.id === fact.source_id,
                                    ) || null,
                                  )
                                }
                              >
                                依据 ↗
                              </button>
                            </div>
                          ))}
                      </section>
                      <section className="evidence">
                        <h2>来源回查</h2>
                        {source ? (
                          <>
                            <span className="badge">
                              第 {source.page} 页 ·{" "}
                              {source.locator === "page"
                                ? "页级定位"
                                : "文本定位"}
                            </span>
                            <pre>{source.text}</pre>
                            {preview && (
                              <a
                                href={preview}
                                target="_blank"
                                rel="noreferrer"
                                aria-label="打开原尺寸图纸页图"
                              >
                                <img
                                  src={preview}
                                  alt={`来源图纸第 ${source.page} 页`}
                                />
                                <small>打开原尺寸页图 ↗</small>
                              </a>
                            )}
                          </>
                        ) : (
                          <div className="empty">
                            点击字段右侧「依据」，核对原文或图纸页图。
                          </div>
                        )}
                      </section>
                    </div>
                    {run.metrics.model && (
                      <section>
                        <details>
                          <summary>处理详情</summary>
                          <p>
                            {run.metrics.model} ·{" "}
                            {run.metrics.endpoint_host || "服务地址未记录"} ·{" "}
                            {run.metrics.real_model_call
                              ? "已完成"
                              : "调用未完成"}
                          </p>
                          <div className="stats">
                            <article>
                              <small>耗时 / ms</small>
                              <strong>
                                {run.metrics.elapsed_ms ?? "未知"}
                              </strong>
                            </article>
                            <article>
                              <small>输入 tokens</small>
                              <strong>
                                {run.metrics.usage?.input_tokens ?? "未知"}
                              </strong>
                            </article>
                            <article>
                              <small>输出 tokens</small>
                              <strong>
                                {run.metrics.usage?.output_tokens ?? "未知"}
                              </strong>
                            </article>
                            <article>
                              <small>
                                估算费用 /{" "}
                                {run.metrics.cost_currency || "未配置"}
                              </small>
                              <strong className="text-stat">
                                {run.metrics.estimated_cost ?? "未知"}
                              </strong>
                            </article>
                          </div>
                          <small>
                            响应 ID：{run.metrics.response_id || "无可用响应"}{" "}
                            {run.metrics.error_code}
                          </small>
                        </details>
                      </section>
                    )}
                    <section>
                      <h2>工程确认与补充依据</h2>
                      <form
                        className="correction"
                        onSubmit={(e) => {
                          e.preventDefault();
                          act(() =>
                            request<Run>(`/runs/${run.id}/fields`, "PATCH", {
                              expected_version: run.version,
                              values: { [fieldKey]: value },
                              reason,
                            }),
                          );
                        }}
                      >
                        <label>
                          字段
                          <select
                            value={fieldKey}
                            onChange={(e) => setFieldKey(e.target.value)}
                          >
                            {Object.entries(names).map(([k, n]) => (
                              <option value={k} key={k}>
                                {n}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          确认值
                          <input
                            value={value}
                            onChange={(e) => setValue(e.target.value)}
                            required
                            maxLength={200}
                          />
                        </label>
                        <label>
                          依据 / 修改原因
                          <input
                            value={reason}
                            onChange={(e) => setReason(e.target.value)}
                            minLength={3}
                            required
                            placeholder="说明客户澄清或复核依据"
                          />
                        </label>
                        <button
                          disabled={
                            busy || !engineer || !Object.keys(run.fields).length
                          }
                        >
                          保存并重新校验
                        </button>
                      </form>
                      <div className="upload">
                        <label>
                          补充正确版次图纸
                          <input
                            type="file"
                            accept=".pdf"
                            onChange={(e) =>
                              setReplacement(e.target.files?.[0] || null)
                            }
                          />
                        </label>
                        <button
                          disabled={
                            busy ||
                            !engineer ||
                            !replacement ||
                            reason.length < 3
                          }
                          onClick={() => {
                            const data = new FormData();
                            data.append("drawing", replacement!);
                            data.append(
                              "expected_version",
                              String(run.version),
                            );
                            data.append("reason", reason);
                            act(() =>
                              request<Run>(
                                `/runs/${run.id}/drawing`,
                                "POST",
                                data,
                              ),
                            );
                          }}
                        >
                          替换图纸
                        </button>
                      </div>
                      <p className="muted">
                        请在上方填写修改原因。换图后需重新识别并计算。
                      </p>
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          !quoteTask ||
                          !engineer ||
                          run.issues.length > 0 ||
                          !Object.keys(run.fields).length
                        }
                        onClick={() => act(() => mutate("cost"), 2)}
                      >
                        计算内部预算 →
                      </button>
                    </section>
                  </>
                )}
              {view === 2 && (
                <section>
                  <div className="section-heading">
                    <h2>成本明细</h2>
                    <button
                      disabled={
                        busy || !engineer || !quoteTask || run.issues.length > 0
                      }
                      onClick={() => act(() => mutate("cost"))}
                    >
                      重新计算
                    </button>
                  </div>
                  {run.quote ? (
                    <>
                      <div className="stats">
                        <article>
                          <small>数量</small>
                          <strong>{run.quote.quantity}</strong>
                        </article>
                        <article>
                          <small>单位成本 / EUR</small>
                          <strong>{run.quote.unit_cost}</strong>
                        </article>
                        <article>
                          <small>单位报价 / EUR</small>
                          <strong>{run.quote.unit_price}</strong>
                        </article>
                        <article className="total">
                          <small>草案总额 / EUR</small>
                          <strong>{run.quote.total_price}</strong>
                        </article>
                      </div>
                      <h3>计价依据</h3>
                      {run.quote.knowledge_basis?.length ? (
                        <ul>
                          {run.quote.knowledge_basis.map((item) => (
                            <li key={item.id}>
                              {item.title} · {item.source_id}@{item.version} ·
                              有效至 {item.valid_until}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="muted">计价依据未记录，请重新计算。</p>
                      )}
                      {run.quote.routing?.length ? (
                        <details>
                          <summary>查看批准工艺路线</summary>
                          <ol>
                            {run.quote.routing.map((step) => (
                              <li key={step.step}>
                                {step.operation} — {step.basis}
                              </li>
                            ))}
                          </ol>
                        </details>
                      ) : null}
                      <table>
                        <thead>
                          <tr>
                            <th>成本项</th>
                            <th>单位成本 EUR</th>
                            <th>依据</th>
                          </tr>
                        </thead>
                        <tbody>
                          {run.quote.lines.map((l) => (
                            <tr key={l.code}>
                              <td>{l.description}</td>
                              <td>{l.unit_cost}</td>
                              <td>{l.basis}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p className="muted">
                        目标毛利率 {Number(run.quote.margin) * 100}%
                      </p>
                      <div className="provenance">
                        <p>
                          草案 v{run.quote.draft_version} ·{" "}
                          {run.quote.rate_version}
                        </p>
                        <p>{run.quote.template_version}</p>
                        <details>
                          <summary>版本校验信息</summary>
                          <pre>{run.quote.reference_hash}</pre>
                        </details>
                      </div>
                      <h3>待确认 / 未包含</h3>
                      <ul>
                        {run.quote.pending.map((p) => (
                          <li key={p}>{p}</li>
                        ))}
                      </ul>
                      <button className="primary" onClick={() => setView(3)}>
                        前往审批 →
                      </button>
                    </>
                  ) : (
                    <p className="empty">
                      完成抽取与工程问题处理后，计算成本草案。
                    </p>
                  )}
                </section>
              )}
              {view === 2 &&
                (run.inputs || Object.keys(run.fields).length > 0) && (
                  <IntegrationPreview
                    key={`${run.id}:${run.version}:${token}`}
                    version={run.version}
                    load={() => request(`/runs/${run.id}/integration-preview`)}
                  />
                )}
              {view === 3 && (
                <>
                  <section>
                    <h2>审批 · 内部预算草案</h2>
                    {run.approval ? (
                      <div className="alert">
                        已由 {run.approval.actor} 批准 · {run.approval.note}
                      </div>
                    ) : (
                      <>
                        <label className="check">
                          <input
                            type="checkbox"
                            checked={confirmed}
                            onChange={(e) => setConfirmed(e.target.checked)}
                          />
                          我已复核材料、版次、孔数与位置、尺寸和计价依据，确认仅作为内部预算。
                        </label>
                        <label>
                          审批备注
                          <textarea
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            placeholder="记录工程与报价复核结论"
                          />
                        </label>
                        <button
                          className="primary"
                          disabled={
                            busy ||
                            engineer ||
                            !confirmed ||
                            note.length < 3 ||
                            run.state !== "READY_FOR_APPROVAL"
                          }
                          onClick={() =>
                            act(() =>
                              mutate("approve", {
                                technical_confirmed: true,
                                internal_only: true,
                                note,
                              }),
                            )
                          }
                        >
                          批准当前草案
                        </button>
                      </>
                    )}
                    <div className="actions">
                      <button
                        disabled={busy || engineer || !run.approval}
                        onClick={() => act(() => mutate("export"))}
                      >
                        生成导出文件
                      </button>
                      <button disabled={!run.export} onClick={download}>
                        下载草案
                      </button>
                    </div>
                  </section>
                  <section>
                    <h2>审计时间线</h2>
                    {run.events?.map((event) => (
                      <details className="event" key={event.sequence}>
                        <summary>
                          <span>#{event.sequence}</span>
                          <strong>{event.action}</strong>
                          <small>
                            {event.actor} ·{" "}
                            {new Date(event.occurred_at).toLocaleString()}
                          </small>
                        </summary>
                        <pre>{JSON.stringify(event.detail, null, 2)}</pre>
                      </details>
                    ))}
                  </section>
                </>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  );
}
