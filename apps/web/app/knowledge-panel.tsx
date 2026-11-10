"use client";

import { useEffect, useRef, useState } from "react";

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
const reasons: Record<string, string> = {
  UNAPPROVED: "尚未批准",
  OUTSIDE_VALIDITY: "不在有效期内",
  REFERENCE_ONLY: "历史 / 参考资料，不能用于当前计价",
  MISMATCH_MATERIAL: "材料不适用",
  MISMATCH_REVISION: "版次不适用",
  MISMATCH_PART_NUMBER: "零件不适用",
  MISMATCH_UNIT: "单位不适用",
  MISMATCH_CURRENCY: "币种不适用",
  PAYLOAD_VERSION_MISMATCH: "知识版本与执行资料不一致",
  INVALID_MANUFACTURING_PAYLOAD: "制造资料无效或过期",
  PAYLOAD_NOT_APPLICABLE: "制造资料不适用于当前条件",
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
        <h2>作用域内的工程知识</h2>
        <p>
          PM-DEMO / 德国 DE-DEMO / mounting-bracket · PM-BRKT-010 · EUR / mm
        </p>
        <p className="muted">
          先校验权限和适用范围，再匹配关键词。检索结果不会自动修改报价；成本只能来自后端批准且有效的资料。
        </p>
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
      {searched && (
        <p className="muted">
          当前授权范围内匹配 {items.length} 条 · 仅供参考的资料已明确标注 ·
          全部为合成演示数据
        </p>
      )}
      {searched && !items.length && (
        <section className="empty">
          没有当前权限范围内的匹配资料。请调整查询；不会从其他项目或工厂补取。
        </section>
      )}
      {items.map((item) => (
        <section key={item.id}>
          <div className="section-heading">
            <h2>{item.title}</h2>
            <span className="badge">
              {item.eligible_for_cost ? "批准依据候选" : "仅供参考 · 禁止计价"}
            </span>
          </div>
          <p>{item.content}</p>
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
                <li key={reason}>{reasons[reason] || reason}</li>
              ))}
            </ul>
          )}
          <details>
            <summary>查看来源指纹</summary>
            <pre>{item.record_hash}</pre>
          </details>
        </section>
      ))}
    </>
  );
}
