# PrecisionMotion RFQ Workbench

当前已实现 **M0–M4**。先读 [完整项目手册](IndustrialMindAI-项目手册.md)，面试排练使用 [模块、架构与演示讲解](docs/面试与演示讲解.md)。两份文档支持 Obsidian 双向链接、嵌入与 Mermaid 图表。

面向 IndustrialMind Option B 的本地全栈原型：RFQ/图纸 → 来源与问题 → 确定性预算 → 人工审核 → 内部草案导出。

**所有内置资料均为合成数据；输出不能作为正式客户报价或生产指令。** 实施范围以 [[IndustrialMindAI-OptionB-RFQ工作台-roadmap]] 为准。

- 代码：`apps/api`（FastAPI/Python）、`apps/web`（Next.js/TypeScript）。
- 数据：`fixtures/dev`、`fixtures/reference`；标准答案单独保存在 `evals/gold`。
- 技术文档：[入口](docs/README.md)、[运行手册](docs/runbook.md)、[里程碑状态](docs/milestones.md)。
- 开发流程：[Git 与编码规范](docs/development.md)。

## 环境

Python 3.12+；Node.js 20.9+。Windows 本机现有 Node 16 不满足 Next.js 要求；可使用已安装的新版本 Node，或运行手册中列出的 Codex bundled Node。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pytest -q
```

M0 数据可用 `scripts/generate_fixtures.py` 重建，公开 schema 可用 `scripts/export_schemas.py` 重建。不要把评测答案加入检索目录。

真实多模态配置使用根目录 `.env.local`，参考 `.env.example`；密钥只供后端读取。缺少凭据时可开发和验证确定性核心，不能将离线结果标为真实模型验收。

当前 M0/M1 已验收提交；M2 真实模型闭环已验收；M3 证据与稳健性已完成，见 [M3 验收](docs/acceptance-m3.md)。使用 `scripts/start-api.ps1` 与 `scripts/start-web.ps1` 两终端启动，Node 参数见 [运行手册](docs/runbook.md)。界面地址 `http://127.0.0.1:3000`。

[验收记录与限制](docs/acceptance-m2.md) · [演示步骤](docs/demo.md)。工程管理已包含锁定依赖、格式检查、测试、生成契约、ADR、计划快照、分支记录与 CI 定义；系统仍为本地演示原型。
