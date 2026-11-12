"use client";

import { useEffect, useRef, useState } from "react";

type AgentRun = {
  id: string;
  status:
    | "ready"
    | "running"
    | "waiting"
    | "model_paused"
    | "completed"
    | "exhausted"
    | "stopped";
  intent: string | null;
  plan: string[];
  documents: { id: string; name: string }[];
  selection: Record<string, string>;
  case_id: string | null;
  question: { question: string; audience: string; options: string[] } | null;
  service_issue?: { code: string; message: string } | null;
  quote: { total_price: string; quantity: number } | null;
  risks: { text: string; source_id: string }[];
  reply: string | null;
  draft_questions: string | null;
  summary: string | null;
  limits: Record<string, number>;
  usage: Record<string, number>;
  estimated_cost: string | null;
  cost_partial: boolean;
  steps: {
    id: string;
    call?: { name: string; arguments: object };
    result: object | null;
    error: { code: string; message: string } | null;
  }[];
};

const labels: Record<string, string> = {
  get_case_state: "查看任务状态",
  read_document: "读取附件",
  view_page: "查看原页",
  classify_attachments: "判断任务和附件类型",
  extract_fields: "提取工程字段",
  search_knowledge: "检索受控知识",
  lookup_plm_revision: "查询发布版次",
  lookup_erp_rate: "查询成本与费率",
  run_validation: "校验证据与工程规则",
  calculate_cost: "生成内部报价草案",
  draft_reply: "拟写客户回复",
  draft_questions: "拟写澄清问题",
  flag_risk: "记录风险",
  ask_human: "请求人工答复",
  review_package: "复核交付包",
  finish: "完成任务",
  ready: "可继续",
  running: "执行中",
  waiting: "待人工答复",
  model_paused: "模型服务暂停",
  completed: "已完成",
  exhausted: "预算已用完",
  stopped: "已停止",
  prepare_quote: "准备内部报价",
  review_drawing: "图纸审查",
};

const storageKey = "rfq-workbench-agent-run";

export default function AgentPanel({
  request,
  openCase,
}: {
  request: <T>(
    path: string,
    method?: string,
    body?: object | FormData,
  ) => Promise<T>;
  openCase: (id: string) => void;
}) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [answer, setAnswer] = useState("");
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;
    const id = localStorage.getItem(storageKey);
    if (id)
      request<AgentRun>(`/agent/runs/${id}`)
        .then((current) => {
          if (!cancelled.current) setRun(current);
        })
        .catch(() => localStorage.removeItem(storageKey));
    return () => {
      cancelled.current = true;
    };
  }, [request]);

  async function execute(start: () => Promise<AgentRun>) {
    cancelled.current = false;
    setError("");
    setBusy(true);
    try {
      let current = await start();
      localStorage.setItem(storageKey, current.id);
      if (!cancelled.current) setRun(current);
      while (
        !cancelled.current &&
        ["ready", "running"].includes(current.status)
      ) {
        current = await request<AgentRun>(
          `/agent/runs/${current.id}/step`,
          "POST",
        );
        if (!cancelled.current) setRun(current);
      }
    } catch (exc) {
      if (!cancelled.current)
        setError(exc instanceof Error ? exc.message : "Agent 执行失败");
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!run) return;
    cancelled.current = true;
    try {
      setRun(await request<AgentRun>(`/agent/runs/${run.id}/stop`, "POST"));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "停止失败");
    }
  }

  function submit(path: string, field: string, value: string) {
    const payload = new FormData();
    payload.set(field, value);
    files.forEach((file) => payload.append("attachments", file));
    setFiles([]);
    void execute(() => request<AgentRun>(path, "POST", payload));
  }

  return (
    <>
      {error && (
        <p role="alert" className="alert danger">
          {error}
        </p>
      )}
      {!run ? (
        <section>
          <form
            className="correction"
            onSubmit={(event) => {
              event.preventDefault();
              submit("/agent/runs", "request", question);
            }}
          >
            <label>
              工程请求
              <textarea
                value={question}
                maxLength={2500}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="粘贴询价邮件或说明任务目标"
              />
            </label>
            <label>
              附件
              <input
                type="file"
                accept=".pdf,.md,.txt"
                multiple
                onChange={(event) =>
                  setFiles(Array.from(event.target.files ?? []))
                }
              />
            </label>
            <button
              className="primary"
              disabled={busy || (!question.trim() && !files.length)}
            >
              运行 Agent
            </button>
          </form>
        </section>
      ) : (
        <>
          <section>
            <div className="section-heading">
              <h2>{labels[run.intent ?? ""] || "工程任务"}</h2>
              <span className="badge" role="status">
                {busy && run.status === "ready" ? "执行中" : labels[run.status]}
              </span>
            </div>
            {run.summary && <p>{run.summary}</p>}
            {run.status === "model_paused" && (
              <p role="alert" className="alert danger">
                {run.service_issue?.message ?? "模型服务暂不可用。"}
              </p>
            )}
            {!!run.plan.length && (
              <div>
                <h3>处理计划</h3>
                <ol>
                  {run.plan.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ol>
              </div>
            )}
            {!!run.documents.length && (
              <ul>
                {run.documents.map((document) => (
                  <li key={document.id}>
                    {document.id} · {document.name}
                    {run.selection.rfq === document.id && " · RFQ"}
                    {run.selection.drawing === document.id && " · 图纸"}
                  </li>
                ))}
              </ul>
            )}
            <p>
              步骤 {run.usage.steps}/{run.limits.steps} · Token{" "}
              {run.usage.tokens}/{run.limits.tokens}
              {" · "}耗时 {Math.round(run.usage.seconds)}/{run.limits.seconds}{" "}
              秒{" · "}错误 {run.usage.errors}/{run.limits.errors}
            </p>
            {run.estimated_cost && (
              <p>
                已估算费用 {run.estimated_cost}
                {run.cost_partial && "（部分用量未计价）"}
              </p>
            )}
            {!run.estimated_cost && run.cost_partial && <p>模型费用待核算</p>}
            {run.quote && (
              <p>
                内部草案 · {run.quote.quantity} 件 · EUR {run.quote.total_price}
              </p>
            )}
            <div className="actions">
              {run.case_id && (
                <button onClick={() => openCase(run.case_id!)}>
                  打开工程任务
                </button>
              )}
              {["ready", "running"].includes(run.status) && (
                <button
                  disabled={busy}
                  onClick={() => void execute(() => Promise.resolve(run))}
                >
                  继续执行
                </button>
              )}
              {run.status === "model_paused" && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() =>
                    void execute(() =>
                      request<AgentRun>(`/agent/runs/${run.id}/retry`, "POST"),
                    )
                  }
                >
                  {busy ? "重试中…" : "重试模型"}
                </button>
              )}
              {["ready", "running", "waiting", "model_paused"].includes(
                run.status,
              ) && (
                <button onClick={() => void stop()}>停止 Agent</button>
              )}
              {!busy && (
                <button
                  onClick={() => {
                    setRun(null);
                    setAnswer("");
                    localStorage.removeItem(storageKey);
                  }}
                >
                  新建 Agent 任务
                </button>
              )}
            </div>
          </section>
          {run.question && run.status === "waiting" && (
            <section>
              <h2>
                待答复 ·{" "}
                {run.question.audience === "customer" ? "客户" : "工程师"}
              </h2>
              <p>{run.question.question}</p>
              {!!run.question.options.length && (
                <ul>
                  {run.question.options.map((option, index) => (
                    <li key={index}>{option}</li>
                  ))}
                </ul>
              )}
              <form
                className="correction"
                onSubmit={(event) => {
                  event.preventDefault();
                  submit(`/agent/runs/${run.id}/answer`, "answer", answer);
                  setAnswer("");
                }}
              >
                <label>
                  记录答复
                  <textarea
                    value={answer}
                    maxLength={2500}
                    onChange={(event) => setAnswer(event.target.value)}
                  />
                </label>
                <label>
                  补充附件
                  <input
                    type="file"
                    accept=".pdf,.md,.txt"
                    multiple
                    onChange={(event) =>
                      setFiles(Array.from(event.target.files ?? []))
                    }
                  />
                </label>
                <button
                  className="primary"
                  disabled={busy || (!answer.trim() && !files.length)}
                >
                  保存并继续
                </button>
              </form>
            </section>
          )}
          {(run.reply || run.draft_questions || run.risks.length > 0) && (
            <section>
              <h2>交付包</h2>
              {run.reply && (
                <>
                  <h3>客户回复草稿</h3>
                  <pre>{run.reply}</pre>
                </>
              )}
              {run.draft_questions && (
                <>
                  <h3>澄清问题草稿</h3>
                  <pre>{run.draft_questions}</pre>
                </>
              )}
              {!!run.risks.length && (
                <ul>
                  {run.risks.map((risk, index) => (
                    <li key={index}>{risk.text}</li>
                  ))}
                </ul>
              )}
            </section>
          )}
          <section>
            <h2>工具轨迹</h2>
            {run.steps.map((step) => (
              <details className="event" key={step.id}>
                <summary>
                  {labels[step.call?.name ?? ""] ||
                    step.call?.name ||
                    "模型调用"}
                  {step.error && ` · ${step.error.message}`}
                </summary>
                <pre>{JSON.stringify(step.error ?? step.result, null, 2)}</pre>
              </details>
            ))}
          </section>
        </>
      )}
    </>
  );
}
