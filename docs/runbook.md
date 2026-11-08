# 本地运行手册

Owner: Engineering · Status: M0 bootstrap · Updated: 2026-11-08

## 环境与数据

Python 3.12+，Node 20.9+。在项目根目录建立 `.venv`，安装 `requirements.lock` 和 editable package；依赖锁不包含本机绝对路径。

重建资料：`python scripts/generate_fixtures.py`；重建 schema：`python scripts/export_schemas.py`。必须使用项目虚拟环境 Python。合成 PDF 使用 ReportLab，视觉检查采用 Poppler。

本机可用 Node：`C:\Users\z\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`。该路径仅是本机便利选项，项目本身不依赖 Codex。

## 配置

将 `.env.example` 复制为 `.env.local`；配置模型 key/model 后才启用 live 调用。配置文件、缓存、数据库、上传文件与日志均被 Git 忽略。不在浏览器保存或传送模型密钥。

服务启动、演示与恢复步骤在 M1/M2 实现后同步补齐。未实现的步骤不会在记录中标为已验证。
