# 里程碑验收记录

Owner: Engineering · Updated: 2026-11-08

| 里程碑 | 本地分支 | 当前状态 | 证据与限制 |
|---|---|---|---|
| M0 | `codex/m0-data-contracts` | 通过（合成资料技术验收） | 6 开发包、5 原创 PDF、6 schema、来源 manifest、隔离 gold；8 项测试通过；5 份 PDF 已渲染检查，无裁切/重叠；非客户工程认证 |
| M1 | `codex/m1-deterministic-core` | 通过 | Decimal 计价、SQLite、角色权限、审批失效、乐观锁、导出；19 项累计测试通过，Ruff 通过 |
| M2 | `codex/m2-rfq-workbench` | 通过（合成演示） | 31 项测试、生产构建与浏览器闭环通过；详见 [[acceptance-m2]] |

原始计划基线与 hash 见 `baselines/manifest.json`。提交哈希在下一里程碑或交付记录中填写，避免提交内自引用。用户原有工作区文档修改不混入实现提交。

- M0 提交：`171433a`。
- M1 验收命令：`python -m pytest -q`（19 passed）、`python -m ruff check apps/api scripts tests`；2026-11-08 本机 Python 3.12。
- 上游 Starlette 对 httpx 测试客户端发出弃用提示；当前请求与断言均通过，不影响运行服务。

- M1 提交：`caa8fc5`。
- M2 本地验收日期：2026-11-09；真实调用及同任务审批导出已通过，详见 [[m2-live-result]]。
