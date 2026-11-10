"use client";

import { useState } from "react";

type Preview = {
  run_version: number;
  plm: {
    released_revision: string;
    rfq_revision: string | null;
    drawing_revision: string | null;
  };
  erp: {
    draft_version: number;
    quantity: number;
    currency: string;
    total_price: string;
    approval_status: "PENDING" | "APPROVED";
  } | null;
  erp_status: "NO_DRAFT" | "STALE_DRAFT" | "AVAILABLE";
  mes: {
    revision: string;
    operation: string;
    planned_minutes_per_part: string;
    observed_minutes_per_part: string;
  };
};

export default function IntegrationPreview({
  version,
  load,
}: {
  version: number;
  load: () => Promise<Preview>;
}) {
  const [result, setResult] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const preview = await load();
      if (preview.run_version !== version) {
        setError("任务版本已变化，请刷新任务。");
      } else {
        setResult(preview);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法加载预览。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="section-heading">
        <h2>系统预览 · 模拟</h2>
        <button disabled={busy} onClick={refresh}>
          {busy ? "加载中…" : "加载预览"}
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {result && (
        <>
          <table>
            <tbody>
              <tr>
                <th scope="row">PLM 版次</th>
                <td>
                  发布 {result.plm.released_revision} · RFQ{" "}
                  {result.plm.rfq_revision ?? "—"}
                  {" · 图纸 "}
                  {result.plm.drawing_revision ?? "—"}
                </td>
              </tr>
              <tr>
                <th scope="row">ERP 报价</th>
                <td>
                  {result.erp
                    ? `草案 v${result.erp.draft_version} · ${result.erp.quantity} 件 · ${result.erp.currency} ${result.erp.total_price} · ${result.erp.approval_status === "APPROVED" ? "已批准" : "待审批"}`
                    : result.erp_status === "STALE_DRAFT"
                      ? "草案已过期，请重新计算。"
                      : "暂无报价草案"}
                </td>
              </tr>
              <tr>
                <th scope="row">MES 工时</th>
                <td>
                  {result.mes.operation} · {result.mes.revision} 版 · 计划{" "}
                  {result.mes.planned_minutes_per_part}
                  {" / 样例 "}
                  {result.mes.observed_minutes_per_part} 分钟/件
                </td>
              </tr>
            </tbody>
          </table>
          <details>
            <summary>载荷详情</summary>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </details>
        </>
      )}
    </section>
  );
}
