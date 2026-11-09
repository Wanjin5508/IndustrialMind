---
owner: Engineering
status: passed-synthetic-demo
updated: 2026-11-09
---
# M2 真实调用验收

用户配置并授权验证的 DeepSeek `deepseek-flash`，使用 Responses API 实际发送 DEV-001 文本和 PNG 图纸页图。请求成功，返回 13 个字段，与原始合成资料逐项一致；无阻断项。

- 任务：`0683fcb760744ab59b588066629e487a`。
- 响应：`bf251cc6-4bce-4e33-8ad1-c5bef0e2a54c`。
- 抽取耗时：7,453 ms；输入 3,235 tokens，输出 740 tokens（其中 reasoning 214），总计 3,975。
- 同一真实抽取任务经确定性计价、演示审批、导出，状态 `EXPORTED_DRAFT`，100 件总价 EUR 2,547.00。
- 审批为开发验收代理对原创合成资料的确认，不是客户工程签字；既有浏览器闭环与冲突恢复测试见 [[acceptance-m2]]。
- 此时尚未配置费用表，费用未知；不能将未知费用记为零。M3 补充可追溯费用估算。

首次受限环境无法连接外部服务；获得工具网络授权后调用成功，未修改用户密钥。完整运行记录在被 Git 忽略的 `var/acceptance/live-result.json`。这是一例开发验收，不是冻结集准确率。
