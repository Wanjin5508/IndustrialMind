"use client";

import { useState } from "react";
import { knowledgeSummary, knowledgeReasons } from "./knowledge-copy";

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
  ["review_drawing", "图纸审查"],
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
      <h2>新建任务</h2>
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
            <option value="">请选择任务类型</option>
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
              placeholder="留空查询全部资料"
            />
          </label>
        ) : (
          intent && (
            <label>
              样例资料
              <select
                aria-label="任务样例"
                value={fixture}
                onChange={(e) => setFixture(e.target.value)}
              >
                <option value="">稍后上传</option>
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
          创建任务
        </button>
      </form>
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
        任务 ·{" "}
        {intents.find(([value]) => value === task.intent)?.[1] || "待选择类型"}
      </h2>
      {task.request && <p>{task.request}</p>}
      {task.pause && (
        <div role="status" className="alert">
          <strong>待处理</strong>
          <p>
            {task.pause.code === "MISSING_INPUT"
              ? "请补充以下内容。"
              : task.pause.code === "ENGINEERING_REVIEW"
                ? "请处理工程问题后继续。"
                : task.pause.message}
          </p>
          {task.pause.missing.length > 0 && (
            <p>
              待补充：
              {task.pause.missing
                .map(
                  (field) =>
                    ({
                      intent: "任务类型",
                      query: "查询条件",
                      drawing: "图纸",
                      rfq: "RFQ",
                    })[field] || field,
                )
                .join(" / ")}
            </p>
          )}
        </div>
      )}
      {!task.intent && (
        <label>
          任务类型
          <select
            aria-label="任务类型"
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
            placeholder="留空查询全部资料"
          />
        </label>
      )}
      {fileTask && (
        <label>
          识别方式
          <select
            aria-label="任务抽取模式"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="local_text">本地文本识别</option>
            <option value="live" disabled={!live}>
              在线图文识别
            </option>
          </select>
        </label>
      )}
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
        继续处理
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
            保存资料
          </button>
        </form>
      )}
      {task.result?.summary && (
        <div className="alert">
          <strong>图纸字段审查完成</strong>
          <p>待复核：GD&T、孔位、可制造性、客户要求及交期。</p>
        </div>
      )}
      {task.result?.results && (
        <div>
          <h3>查询结果 · {task.result.results.length} 条</h3>
          <p className="muted">重新查询请新建任务。</p>
          {task.result.results.map((item) => (
            <article key={item.id}>
              <h3>{item.title}</h3>
              <small>
                {item.id} · {item.version} ·{" "}
                {item.eligible_for_cost ? "批准依据" : "仅供参考"}
              </small>
              <p>{knowledgeSummary(item.content)}</p>
              <p className="muted">
                {item.reasons
                  .map((reason) => knowledgeReasons[reason] || reason)
                  .join(" / ")}
              </p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
