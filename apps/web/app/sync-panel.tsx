"use client";

import { useEffect, useRef, useState } from "react";

type Intent = {
  id: string;
  operation: string;
  state: string;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string | null;
  last_error_code: string | null;
  receipt: {
    external_id: string;
    target_version: string;
    acked_at: string;
  } | null;
  reconciliation: {
    status: string;
    checked_at: string;
    diffs: { field: string; expected: string | null; actual: string | null }[];
  } | null;
  attempt_log: object[];
};

export type SyncPanelProps = {
  runId: string;
  version: number;
  approved: boolean;
  approver: boolean;
  request: <T>(
    path: string,
    method?: string,
    body?: object | FormData,
    headers?: Record<string, string>,
  ) => Promise<T>;
  onChanged: () => Promise<void>;
};

const labels: Record<string, string> = {
  PREPARED: "待派发",
  DISPATCHING: "派发中",
  RETRY_WAIT: "等待重试",
  UNKNOWN_OUTCOME: "结果待确认",
  ACKED: "已收到回执",
  VERIFIED: "对账通过",
  DIVERGED: "目标数据有差异",
  HELD: "发送条件待处理",
  REJECTED: "目标拒绝",
  QUARANTINED: "已隔离",
  SUPERSEDED: "已终止",
};

export default function SyncPanel({
  runId,
  version,
  approved,
  approver,
  request,
  onChanged,
}: SyncPanelProps) {
  const [intents, setIntents] = useState<Intent[]>([]);
  const [snapshots, setSnapshots] = useState<object[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pendingKey = useRef<{ key: string; note: string } | null>(null);
  const mounted = useRef(true);

  async function load() {
    const data = await request<{ intents: Intent[]; snapshots: object[] }>(
      "/runs/" + runId + "/sync",
    );
    if (mounted.current) {
      setIntents(data.intents);
      setSnapshots(data.snapshots ?? []);
    }
  }

  useEffect(() => {
    mounted.current = true;
    load().catch((e) => {
      if (mounted.current) setError(e.message);
    });
    return () => {
      mounted.current = false;
    };
    // Parent key includes the task, version and authenticated role.
  }, [runId, version]);

  async function perform(action: () => Promise<unknown>, changesRun = false) {
    setBusy(true);
    setError("");
    try {
      await action();
      if (!mounted.current) return;
      await load();
      if (changesRun) await onChanged();
    } catch (e) {
      if (mounted.current) {
        setError(e instanceof Error ? e.message : "同步操作失败");
        await load().catch(() => {});
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  function enqueue() {
    if (!pendingKey.current || pendingKey.current.note !== note)
      pendingKey.current = { key: crypto.randomUUID(), note };
    return request(
      "/runs/" + runId + "/sync",
      "POST",
      {
        expected_version: version,
        target: "ERP",
        note,
      },
      { "Idempotency-Key": pendingKey.current.key },
    );
  }

  return (
    <section>
      <div className="section-heading">
        <h2>ERP 草案同步 · 模拟</h2>
        <span className="badge">SIMULATED / NO EXTERNAL WRITE</span>
      </div>
      <button disabled={busy} onClick={() => perform(load)}>
        刷新同步记录
      </button>
      {error && (
        <p role="alert" className="alert danger">
          {error}
        </p>
      )}
      {approver && (
        <div className="actions">
          <label>
            同步 / 决议备注
            <input
              aria-label="同步决议备注"
              value={note}
              maxLength={1000}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <button
            disabled={busy || !approved || note.trim().length < 3}
            onClick={() => perform(enqueue, true)}
          >
            请求同步
          </button>
        </div>
      )}
      {intents.length === 0 && <p>暂无同步意图</p>}
      {snapshots.length > 0 && (
        <details>
          <summary>来源快照 · {snapshots.length} 份</summary>
          <pre>{JSON.stringify(snapshots, null, 2)}</pre>
        </details>
      )}
      {intents.map((intent) => (
        <article key={intent.id}>
          <h3>{labels[intent.state] || intent.state}</h3>
          <p>
            {intent.operation === "UPDATE_DRAFT" ? "更新草案" : "创建草案"}
            {" · 尝试 "}
            {intent.attempts}/{intent.max_attempts}
          </p>
          {intent.last_error_code && <p>{intent.last_error_code}</p>}
          {intent.next_attempt_at && intent.state === "RETRY_WAIT" && (
            <p>下次尝试时间 {intent.next_attempt_at}</p>
          )}
          {intent.receipt && (
            <p>
              目标记录 {intent.receipt.external_id} · 版本{" "}
              {intent.receipt.target_version}
              {" · "}
              {intent.receipt.acked_at}
            </p>
          )}
          {intent.reconciliation && (
            <div>
              <p>
                最近对账 {intent.reconciliation.status} ·{" "}
                {intent.reconciliation.checked_at}
              </p>
              {intent.reconciliation.diffs.length > 0 && (
                <table>
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>待发送值</th>
                      <th>目标值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {intent.reconciliation.diffs.map((diff) => (
                      <tr key={diff.field}>
                        <td>{diff.field}</td>
                        <td>{diff.expected ?? "—"}</td>
                        <td>{diff.actual ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
          {approver && (
            <div className="actions">
              {["PREPARED", "RETRY_WAIT", "HELD"].includes(intent.state) && (
                <button
                  disabled={busy}
                  onClick={() =>
                    perform(() =>
                      request("/sync/" + intent.id + "/dispatch", "POST"),
                    )
                  }
                >
                  {intent.state === "PREPARED" ? "派发一次" : "重检并重试"}
                </button>
              )}
              {["DISPATCHING", "UNKNOWN_OUTCOME", "ACKED", "VERIFIED"].includes(
                intent.state,
              ) && (
                <button
                  disabled={busy}
                  onClick={() =>
                    perform(() =>
                      request("/sync/" + intent.id + "/reconcile", "POST"),
                    )
                  }
                >
                  查询并对账
                </button>
              )}
              {["DIVERGED", "QUARANTINED", "HELD"].includes(intent.state) && (
                <>
                  <button
                    disabled={busy || note.trim().length < 3}
                    onClick={() =>
                      perform(
                        () =>
                          request("/sync/" + intent.id + "/resolve", "POST", {
                            decision: "rebase",
                            note,
                          }),
                        true,
                      )
                    }
                  >
                    重新建立依据并审批
                  </button>
                  <button
                    disabled={busy || note.trim().length < 3}
                    onClick={() =>
                      perform(() =>
                        request("/sync/" + intent.id + "/resolve", "POST", {
                          decision: "abandon",
                          note,
                        }),
                      )
                    }
                  >
                    终止此意图
                  </button>
                </>
              )}
            </div>
          )}
          <details>
            <summary>意图与尝试详情</summary>
            <pre>{JSON.stringify(intent, null, 2)}</pre>
          </details>
        </article>
      ))}
    </section>
  );
}
