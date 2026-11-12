"use client";

import { useEffect, useRef, useState } from "react";
import { knowledgeSummary, knowledgeReasons } from "./knowledge-copy";

export type KnowledgeItem = {
  id: string;
  source_id: string;
  title: string;
  kind: string;
  version: string;
  content: string;
  eligible_for_cost: boolean;
  reasons: string[];
  valid_from: string;
  valid_until: string;
  record_hash: string;
  material: string;
  revisions: string[];
  approved: boolean;
};

export default function KnowledgePanel({ token }: { token: string }) {
  const [query, setQuery] = useState(""),
    [material, setMaterial] = useState("Al-6061-T6"),
    [revision, setRevision] = useState("B");
  const [kind, setKind] = useState(""),
    [items, setItems] = useState<KnowledgeItem[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [searched, setSearched] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => {
    setItems([]);
    setSearched(false);
    setError("");
    return () => pending.current?.abort();
  }, [token]);
  async function search() {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError("");
    setItems([]);
    setSearched(false);
    try {
      const response = await fetch("/api/knowledge/search", {
        signal: controller.signal,
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Demo-Token": token },
        body: JSON.stringify({ query, material, revision, kind: kind || null }),
      });
      const result = await response.json();
      if (controller.signal.aborted) return;
      if (!response.ok)
        throw new Error(result.error?.message || "知识查询失败");
      setItems(result.results);
      setSearched(true);
    } catch (e) {
      if (controller.signal.aborted) return;
      setError(e instanceof Error ? e.message : "知识查询失败");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return (
    <>
      <section>
        <h2>工程资料</h2>
        <p>PM-BRKT-010 · 德国工厂 · EUR / mm</p>
        <form
          className="correction"
          onSubmit={(e) => {
            e.preventDefault();
            search();
          }}
        >
          <label>
            关键词
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={200}
              placeholder="材料、工艺、费率、历史，留空查全部"
            />
          </label>
          <label>
            材料
            <select
              value={material}
              onChange={(e) => setMaterial(e.target.value)}
            >
              <option>Al-6061-T6</option>
              <option>Steel-S235</option>
            </select>
          </label>
          <label>
            图纸版次
            <select
              value={revision}
              onChange={(e) => setRevision(e.target.value)}
            >
              <option>B</option>
              <option>A</option>
            </select>
          </label>
          <label>
            资料类型
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">全部</option>
              <option value="catalog">材料目录</option>
              <option value="template">工艺模板</option>
              <option value="rate">费率</option>
              <option value="history">历史案例</option>
            </select>
          </label>
          <button className="primary" disabled={busy}>
            {busy ? "查询中…" : "查询受控知识"}
          </button>
        </form>
      </section>
      {error && (
        <div role="alert" className="alert danger">
          {error}
        </div>
      )}
      {searched && <p className="muted">找到 {items.length} 条资料</p>}
      {searched && !items.length && (
        <section className="empty">
          没有匹配资料，请调整关键词或筛选条件。
        </section>
      )}
      {items.map((item) => (
        <section key={item.id}>
          <div className="section-heading">
            <h2>{item.title}</h2>
            <span className="badge">
              {item.eligible_for_cost ? "可用于计价" : "参考资料"}
            </span>
          </div>
          <p>{knowledgeSummary(item.content)}</p>
          <p className="muted">
            {item.source_id} · {item.kind} · {item.version} ·{" "}
            {item.approved ? "已批准" : "未批准"}
          </p>
          <p className="muted">
            {item.material} / 版次 {item.revisions.join(", ")} / 有效期{" "}
            {item.valid_from} — {item.valid_until}
          </p>
          {!!item.reasons.length && (
            <ul>
              {item.reasons.map((reason) => (
                <li key={reason}>{knowledgeReasons[reason] || reason}</li>
              ))}
            </ul>
          )}
          <details>
            <summary>来源校验信息</summary>
            <pre>{item.record_hash}</pre>
          </details>
        </section>
      ))}
    </>
  );
}
