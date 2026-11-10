"use client";

import { useState } from "react";

export type TaskContext = {
  intent: string | null;
  owner: string;
  request: string;
  query: object | null;
  pause: { code: string; message: string; missing: string[] } | null;
  result: {
    summary?: string;
    limitations?: string[];
    results?: {
      id: string;
      title: string;
      content: string;
      version: string;
      eligible_for_cost: boolean;
      reasons: string[];
    }[];
  } | null;
};

const intents = [
  ["prepare_quote", "准备内部报价"],
  ["review_drawing", "只审查图纸"],
  ["search_knowledge", "查询受控知识"],
];

export function CreateTask({
  engineer,
  busy,
  fixtures,
  create,
}: {
  engineer: boolean;
  busy: boolean;
  fixtures: { id: string; label: string }[];
  create: (payload: object) => void;
}) {
  const [intent, setIntent] = useState(
    engineer ? "prepare_quote" : "search_knowledge",
  );
  const [request, setRequest] = useState("");
  const [fixture, setFixture] = useState("");
  const [query, setQuery] = useState("");
  return (
    <section>
      <h2>统一任务入口</h2>
      <p className="muted">
        选择目标 → 补齐资料 → 显式执行 → 人工复核。创建任务不会调用模型。
      </p>
      <form
        className="upload"
        onSubmit={(e) => {
          e.preventDefault();
          create({
            intent: intent || null,
            request,
            ...(intent === "search_knowledge" ? { query: { query } } : {}),
            ...(fixture && ["prepare_quote", "review_drawing"].includes(intent)
              ? { fixture_id: fixture }
              : {}),
          });
        }}
      >
        <label>
          任务目标
          <select
            aria-label="任务目标"
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
          >
            <option value="">尚不明确 · 请求澄清</option>
            {intents.map(([value, label]) => (
              <option
                key={value}
                value={value}
                disabled={!engineer && value !== "search_knowledge"}
              >
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          请求说明
          <input
            aria-label="请求说明"
            maxLength={1000}
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            placeholder="例如：检查这张图纸哪些信息妨碍报价"
          />
        </label>
        {intent === "search_knowledge" ? (
          <label>
            知识关键词
            <input
              aria-label="任务知识关键词"
              value={query}
              maxLength={200}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="留空表示浏览当前权限内资料"
            />
          </label>
        ) : (
          intent && (
            <label>
              开发样例
              <select
                aria-label="任务样例"
                value={fixture}
                onChange={(e) => setFixture(e.target.value)}
              >
                <option value="">暂不附资料 · 创建后上传</option>
                {fixtures.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.id} · {f.label}
                  </option>
                ))}
              </select>
            </label>
          )
        )}
        <button className="primary" disabled={busy}>
          创建所选任务
        </button>
      </form>
      <p className="muted">
        文字说明只作上下文；目标不明确时要求人选择。查询条件默认 PM-BRKT-010 / B
        / Al-6061-T6 / mm / EUR。
      </p>
    </section>
  );
}

export function TaskControls({
  task,
  version,
  state,
  engineer,
  busy,
  live,
  mode,
  setMode,
  resume,
  attach,
}: {
  task: TaskContext;
  version: number;
  state: string;
  engineer: boolean;
  busy: boolean;
  live: boolean;
  mode: string;
  setMode: (mode: string) => void;
  resume: (payload: object) => void;
  attach: (payload: FormData) => void;
}) {
  const [intent, setIntent] = useState("");
  const [query, setQuery] = useState("");
  const [rfq, setRfq] = useState<File | null>(null);
  const [drawing, setDrawing] = useState<File | null>(null);
  const selected = task.intent || intent;
  const fileTask =
    selected === "prepare_quote" || selected === "review_drawing";
  return (
    <section>
      <h2>
        任务路径 ·{" "}
        {intents.find(([value]) => value === task.intent)?.[1] ||
          "等待明确目标"}
      </h2>
      <p>{task.request}</p>
      <p className="muted">
        {!selected
          ? "选择目标后显示对应流程。"
          : selected === "search_knowledge"
            ? "权限过滤 → 检索 → 来源与适用条件"
            : selected === "review_drawing"
              ? "图纸 → 字段与证据 → 工程问题 → 审查结果（不计价）"
              : "RFQ + 图纸 → 抽取 → 工程门禁 → 受控依据 → 计价 → 等待审批"}
      </p>
      {task.pause && (
        <div role="status" className="alert">
          <strong>{task.pause.code}</strong>
          <p>{task.pause.message}</p>
          {task.pause.missing.length > 0 && (
            <p>待补充：{task.pause.missing.join(" / ")}</p>
          )}
        </div>
      )}
      {!task.intent && (
        <label>
          澄清任务目标
          <select
            aria-label="澄清任务目标"
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
          >
            <option value="">请选择</option>
            {intents.map(([value, label]) => (
              <option
                key={value}
                value={value}
                disabled={!engineer && value !== "search_knowledge"}
              >
                {label}
              </option>
            ))}
          </select>
        </label>
      )}
      {selected === "search_knowledge" && !task.query && (
        <label>
          知识关键词
          <input
            aria-label="补充知识关键词"
            value={query}
            maxLength={200}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="留空浏览获准资料"
          />
        </label>
      )}
      {fileTask && (
        <label>
          执行时的抽取模式
          <select
            aria-label="任务抽取模式"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="local_text">LOCAL_TEXT · 本地文本</option>
            <option value="live" disabled={!live}>
              LIVE · 真实多模态
            </option>
          </select>
        </label>
      )}
      <p className="muted">
        {fileTask
          ? "每次执行最多一次抽取；已有字段时沿用人工修正。LIVE 会发送文本和页图至配置的模型。审批和导出需要另行确认。"
          : "查询在当前角色的授权范围内执行，结果保留为本次任务快照，不调用模型。"}
      </p>
      <button
        className="primary"
        disabled={
          busy || state === "EXTRACTING" || !selected || (fileTask && !engineer)
        }
        onClick={() =>
          resume({
            mode,
            ...(!task.intent ? { intent } : {}),
            ...(selected === "search_knowledge" && !task.query
              ? { query: { query } }
              : {}),
          })
        }
      >
        执行 / 恢复任务
      </button>
      {task.intent && fileTask && engineer && (
        <form
          className="upload"
          onSubmit={(e) => {
            e.preventDefault();
            const data = new FormData();
            data.append("expected_version", String(version));
            if (rfq) data.append("rfq", rfq);
            if (drawing) data.append("drawing", drawing);
            attach(data);
          }}
        >
          {task.intent === "prepare_quote" && (
            <label>
              补充 / 替换 RFQ
              <input
                aria-label="补充 RFQ"
                type="file"
                accept=".md,.txt,.pdf"
                onChange={(e) => setRfq(e.target.files?.[0] || null)}
              />
            </label>
          )}
          <label>
            补充 / 替换图纸
            <input
              aria-label="补充图纸"
              type="file"
              accept=".pdf"
              onChange={(e) => setDrawing(e.target.files?.[0] || null)}
            />
          </label>
          <button
            disabled={busy || state === "EXTRACTING" || (!rfq && !drawing)}
          >
            保存资料并使旧草案失效
          </button>
        </form>
      )}
      {task.result?.summary && (
        <div className="alert">
          <strong>{task.result.summary}</strong>
          {task.result.limitations?.map((text) => (
            <p key={text}>{text}</p>
          ))}
        </div>
      )}
      {task.result?.results && (
        <div>
          <h3>查询快照 · {task.result.results.length} 条</h3>
          <p className="muted">
            本次查询时的来源和适用条件；再次查询请新建任务。历史案例仅作参考。
          </p>
          {task.result.results.map((item) => (
            <article key={item.id}>
              <h3>{item.title}</h3>
              <small>
                {item.id} · {item.version} ·{" "}
                {item.eligible_for_cost ? "批准依据" : "仅供参考"}
              </small>
              <p>{item.content}</p>
              <p className="muted">{item.reasons.join(" / ")}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
