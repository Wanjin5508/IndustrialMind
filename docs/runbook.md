# 本地运行手册

Owner: Engineering · Status: M2 local implementation · Updated: 2026-11-09

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

## 启动工作台

首次在 `apps/web` 执行 `npm ci`。确保 PATH 中 Node 为 20.9+，npm 子进程也需要正确版本。

在根目录两个 PowerShell 终端分别执行：

```powershell
.\scripts\start-api.ps1
```

```powershell
.\scripts\start-web.ps1 -NodePath 'C:\Users\z\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
```

打开 `http://127.0.0.1:3000`。已有受支持 Node 可省略 `-NodePath`。Ctrl+C 停止各服务。生产验证：在 `apps/web` 执行 `npm run build`，启动脚本加 `-Production`。

默认 UI 使用工程师/审批人演示令牌。配置 key/model 后重启 API 才能启用 LIVE。运行 `python scripts/live_acceptance.py` 验证真实调用，随后在 UI 继续人工复核和审批；见 [[acceptance-m2]]。

干净演示：在 API 终端设置 `$env:RFQ_DATA_DIR='var/demo-fresh'` 为未使用的新目录再启动，不删除原有任务。测试始终使用临时数据库。

## M3 模型诊断与费用

`MODEL_CONNECTION_FAILED` 检查网络，`MODEL_AUTH_FAILED` 检查密钥，`MODEL_RATE_LIMIT` 等待服务额度恢复，`MODEL_TIMEOUT` 检查服务耗时与超时参数。错误不会打印供应商原始正文。配置更改后重启后端。

默认费率在 `config/model-pricing.json`，可用绝对路径 `RFQ_MODEL_PRICING_FILE` 覆盖。复核期限后自动显示未知，需核对官方价格再更新；估算不是账单。`python scripts/m3_live_acceptance.py` 对三个开发样例发起真实调用，证据保存到当前 RFQ_DATA_DIR 下的 acceptance/m3-live.json。

## M4 知识维护

知识注册表为 fixtures/reference/knowledge.json；制造执行载荷为 fixtures/reference/manufacturing.json。两者版本必须一致。更新前停止演示写操作、检查适用范围与批准状态，运行测试后提交；不编辑数据库以保留旧审批。NO_UNIQUE_BASIS 表示某类有效候选数量不是 1，检查期限、权限、版本与重复记录。查询中的参考资料不会用于价格。

## M5 演示与恢复

在任务总览的“新建任务”中选择任务类型；不确定时选“请选择任务类型”。可以先创建空任务，再按提示补 RFQ 或图纸。保存资料不调用模型；选择模式后点击“继续处理”。报价遇到冲突停住，使用资料与证据中的修正表单或补正确图纸，然后再次恢复。

等待人工的任务会保存到 SQLite，重启后从最近任务打开即可继续。知识任务仅创建角色可见，切错角色会从列表隐藏。若旧任务没有统一恢复面板，使用原先分步按钮。已完成查询要重新查请新建任务；已完成审图可更新图纸或重抽取。

参考文件变更导致 STALE_DRAFT 时刷新，重新计算和审批。模板或费率不再唯一适用时，修正受控资料后再计算。

## M6 识别失败与系统预览

1. 识别失败后，在资料页查看失败原因和尝试次数。可重试错误允许原方式尝试最多三次。停止后可更换识别方式或上传更新的资料。
2. 若服务曾在识别中退出，重启后重新打开任务，待执行期限结束再点“恢复中断任务”，随后继续处理。默认期限为 270 秒，受 `RFQ_MODEL_TIMEOUT_SECONDS` 影响。
3. 在 DEV-001 选择“回放（REPLAY）”并继续处理。页面显示原记录标识，随后进入正常校验、计价和审批。替换为其他文件后需要匹配的回放记录。
4. 打开“成本草案”，点“加载预览”查看模拟 PLM 版次、ERP 报价和 MES 工时。可展开载荷详情核对。

演练超时时，在启动 API 的 PowerShell 中设 `$env:RFQ_SIMULATE_MODEL_TIMEOUT='1'`，建议同时把 `$env:RFQ_DATA_DIR` 指向独立演练目录。以该配置启动后，选择“在线识别 · 模拟超时”。演练结束后关闭服务，移除该会话的变量 `Remove-Item Env:RFQ_SIMULATE_MODEL_TIMEOUT`，按常规方式重启。
