# TalkWithAgent

本地交互 demo（界面暂名 Sidecar）：**任务执行留在 Codex，讨论 agent 在网页中。**

## 启动

需要 Python 3.10+、已登录的 Codex CLI。没有前端构建步骤或第三方 Python 依赖。

```bash
python3 server.py --main-thread <当前-Codex-任务-ID>
```

打开 http://127.0.0.1:8765 。可用 `--port` 换端口，`--data-dir` 指定本地记录目录（默认 `.runtime/`）。不要将此开发服务暴露到公网。

## 可以体验的内容

- 自由讨论：真实调用本机 Codex。首次发送时创建讨论 thread，以后使用同一个 thread id 恢复会话。
- 任务关联：每个网页讨论记录关联一个主任务；展示主任务在检查点上报的简短进展。
- 建议：采纳、不采纳、补充说明；问题：回复或稍后回答。
- 显式回传：点击“回传 Codex”提交结论。消息先存入 SQLite，执行 agent 读取确认后才显示“Codex 已读取”。
- 刷新恢复：讨论、选择、回传队列保存在服务端；输入草稿保存在当前浏览器。
- 决策卡：点击“体验决策卡”。该卡明确标为示例，不暂停真实任务，不向执行 agent 发送示例决定。
- 关联信息里可以恢复之前的讨论。

## Codex 与网页互通

网页 URL 的 `session=` 是讨论记录 id。执行 agent 用它显式同步；它不是 Codex thread id。

```bash
python3 bridge.py --session <讨论记录-ID> publish '已完成接口分析，正在实现页面'
python3 bridge.py --session <讨论记录-ID> pending
# 读取并理解消息后，再确认对应消息；ack 只代表已读取，不代表已执行。
python3 bridge.py --session <讨论记录-ID> ack <消息-ID>
python3 bridge.py --session <讨论记录-ID> publish '本轮任务完成' --status completed
```

主 agent 的新检查点会显示在网页顶部，下一次讨论请求会带上这些进展和已确认事项。页面每 1.2 秒读取已保存状态；记录持久化到 SQLite。重复提交使用 request id 去重。Web 输入不自动变成执行授权；只有明确提交的结论与卡片答复进入回传队列。

## 当前边界

这是可运行的交互与会话 demo，尚未封装成可安装插件。原生桌面主会话采用显式检查点同步，不自动订阅全部历史，不在主 agent 结束后自动唤醒它。`--main-thread` 是关联标识，本身不授予主会话控制能力。

讨论 agent 是服务创建的独立 Codex thread，并非桌面任务树中由 main-agent 原生派生的 sub-agent。主会话只提供已发布的摘要；没有读取未上报的完整主会话。`codex exec` 使用现有本机登录，调用会消耗对应账户额度。

真实的阻塞决策需要后续接入执行端的等待/恢复机制；当前示例卡只验证 UI，不能宣称已阻塞原生 Codex 执行。自动实时回传、原生子任务关联、插件打包是后续接入工作。

讨论调用保持只读 sandbox，复用已保存的讨论 thread。服务绑定 `127.0.0.1`，验证 Host/Origin 与 JSON 自定义请求头；这是单用户本地开发服务，没有多用户认证。讨论记录可能包含任务内容，不应上传 `.runtime/`。

## 验证

```bash
python3 check.py
node --check app.js
```

检查覆盖持久化、重复请求去重、回传确认、非阻塞答复、延后回答、示例与真实队列隔离，以及多会话隔离。浏览器实际聊天需要可用的 Codex 登录和网络。
