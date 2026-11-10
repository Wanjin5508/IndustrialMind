# 开发与 Git 规范

Owner: Engineering · Status: active · Updated: 2026-11-08

## Git 工作流

- 基线从当前 `main` 创建；保留用户已存在的未提交改动，不把它们混入实现提交。
- 累积里程碑分支：`codex/m0-data-contracts` → `codex/m1-deterministic-core` → `codex/m2-rfq-workbench`。后一分支从前一里程碑提交创建；保留三个本地分支便于比较。
- 按逻辑变更使用 Conventional Commits（`feat`、`fix`、`docs`、`test`、`chore`），只 stage 明确相关路径，禁止 `git add .` 吸收用户工作。
- 验收通过才能称为 completed。未通过真实模型验收时保留 M2 的待验收记录，不能标记发布通过。
- 本次仅本地提交与分支，不 push、改写历史或自动合并 main。后续评审时比较相邻分支差异。

## 代码规范

- Python 3.12+，类型标注覆盖公开契约和服务入口；Pydantic 拒绝未知字段；Ruff 检查和格式化。
- TypeScript strict；组件区分请求、表单状态、结果呈现；显式处理错误和提交中状态。
- 英文标识符；注释解释约束和取舍，不逐行复述代码。公开模块/服务使用简短 docstring。
- 金额使用 Decimal 和明确舍入；API 金额为字符串，避免浮点精度丢失。
- 业务校验、审批和存储由后端执行，不能依赖前端按钮禁用或模型自报结论。
- 不把凭据写进代码或 `NEXT_PUBLIC_*`。外部调用仅在显式选择 live 时执行。

## 完成一次变更

关联里程碑/规则 → 实现 → 运行相应测试 → 更新设计/接口/运行说明 → 记录实际证据 → 检查 staged diff → 提交。失败后修复相关行为，再运行受影响检查；不反复扩大无关测试。

## M3 分支与检查

从已验收 M2 `4a58648` 创建 `codex/m3-extraction-evidence`。格式化：Python 使用 Ruff；前端使用项目锁定 Prettier。修改契约后运行两个 schema 导出脚本。真实调用用独立验收脚本 opt-in，自动测试不读取本机密钥。编辑器 `.history/` 不进入 Git，避免本机配置的历史副本被提交。

## M4 分支

从 M3 的 9699b76 创建 codex/m4-controlled-knowledge。本地累计分支保留 M0–M4 检查点；没有自动合并 main 或推送。
