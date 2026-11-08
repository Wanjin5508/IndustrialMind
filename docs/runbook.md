# 本地运行手册

Owner: Engineering · Status: M1 implemented · Updated: 2026-11-08

## 环境与数据

Python 3.12+，Node 20.9+。在项目根目录建立 `.venv`，安装 `requirements.lock` 和 editable package；依赖锁不包含本机绝对路径。

重建资料：`python scripts/generate_fixtures.py`；重建 schema：`python scripts/export_schemas.py`。必须使用项目虚拟环境 Python。合成 PDF 使用 ReportLab，视觉检查采用 Poppler。

本机可用 Node：`C:\Users\z\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`。该路径仅是本机便利选项，项目本身不依赖 Codex。

## 配置

将 `.env.example` 复制为 `.env.local`；配置模型 key/model 后才启用 live 调用。配置文件、缓存、数据库、上传文件与日志均被 Git 忽略。不在浏览器保存或传送模型密钥。

## 启动后端

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn rfq_workbench.main:create_app --factory --host 127.0.0.1 --port 8000
```

健康地址 `http://127.0.0.1:8000/health`，交互 API `http://127.0.0.1:8000/docs`。使用 `X-Demo-Token` 的演示工程师或审批人令牌；M1 可通过 `/runs/confirmed` 提交已确认输入并依次计价、审批和导出。

数据库 `var/workbench.sqlite3` 使用 schema version 1。停止服务后可备份整个 `var`；恢复时使用相同 schema 的服务。未知 schema 拒绝启动，不自动丢弃数据。演示重置优先配置新的 `RFQ_DATA_DIR`，保留已有任务记录。

版本冲突：刷新任务后重新操作。费率或输入变化：重新计价、重新审批。无权限：检查演示角色。不要通过修改数据库绕过审批。
