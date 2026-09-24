# TalkWithAgent

任务执行留在 Codex，讨论在网页中。讨论 agent 绑定使用插件的 Codex session，自动读取该会话的用户消息、可见回复与进展，并能只读查看任务文档、代码和 Git 差异；你和 agent 在同一条对话里交流，无需创建话题或重新介绍任务。

## 安装

需要 Node.js 18+、Python 3.10+，以及已安装并登录的 Codex CLI（支持 `app-server` 的 `thread/read` 和 `exec resume --output-schema`）。npm 包没有第三方运行时依赖，使用本机 Codex 登录。

目前可从 GitHub 安装；尚未发布到 npm 公共仓库：

```bash
npm install -g git+https://github.com/God1007/TalkWithAgent.git
talkwithagent init
talkwithagent install-codex
talkwithagent start
```

`init` 创建配置文件；已有文件会报错，避免覆盖。服务保持在当前终端运行，按 Ctrl+C 停止。默认网页是 http://127.0.0.1:8765 。

`install-codex` 通过 Codex 官方 CLI 注册随包提供的本地 marketplace，再安装 `talkwithagent@talkwithagent`。安装后在**新的 Codex 任务**中使用 `$talkwithagent`。插件负责关联当前任务、发布进展和读取反馈；无需手动复制讨论 session ID。

npm 管理命令和网页服务，Codex 插件提供主任务的协作流程。也可以只使用 npm 命令，不安装插件。插件采用 [Codex 插件兼容格式](https://developers.openai.com/plugins/build/plugins)，包含一个 skill，不依赖额外 MCP 服务。

## 配置

默认配置位于 `~/.config/talkwithagent/config.json`，配置文件内容：

```json
{
  "port": 8765,
  "data_dir": "./data",
  "model": null,
  "auto_discuss": true,
  "max_open_topics": 4,
  "discussion_timeout": 150
}
```

| 配置 | 含义 |
| --- | --- |
| `port` | 本地端口，1–65535；只监听 127.0.0.1 |
| `data_dir` | 会话数据库目录；相对路径以配置文件所在目录为基准 |
| `model` | 讨论模型名称；null 使用 Codex CLI 默认模型，不修改执行任务的模型 |
| `auto_discuss` | 是否主动提出问题和建议；关闭后仍回复用户，仍同步主会话 |
| `max_open_topics` | agent 同时等待回应的问题或建议上限，1–20；保留旧配置名称 |
| `discussion_timeout` | 单次讨论调用超时秒数，10–600 |

数据默认保存在 `~/.config/talkwithagent/data/`，不在 npm 安装目录或 npx 缓存中。更新 npm 包不会清空会话。配置变化在服务重启后生效。

```bash
talkwithagent config
talkwithagent start --port 9000 --no-auto-discuss
talkwithagent init --config ./team-config.json
talkwithagent start --config ./team-config.json
```

配置优先级：启动参数 > 配置文件 > 默认值。用 `--config` 或环境变量 `TALKWITHAGENT_CONFIG` 指定配置文件；命令行的 `--config` 优先。配置文件有未知字段或类型错误时会明确报错。

启动、关联、发布和读取反馈必须使用同一个配置文件。临时改变启动端口时，也应在配置文件中同步端口供后续 bridge 命令读取。Python 不在 PATH 时，可以把 `TALKWITHAGENT_PYTHON` 设为 Python 可执行文件的完整路径。

已有会话可继续使用原数据目录：

```bash
talkwithagent start --data-dir /absolute/path/to/existing-data
```

直接运行 `python3 server.py` 和 `python3 cli.py` 也支持相同配置。旧的 `bridge.py` 命令保留供兼容，它需要单独指定 `--url` 才能连接非默认端口。

## 关联与同步

每个 Codex 任务关联一个网页 session；重复关联返回同一个 session 和链接。网页内可点击「＋ 新增 agent」添加多个讨论 agent，用下拉框切换，也可将各自带 `agent=` 的链接放在不同浏览器标签页中。

所有 agent 自动共享同一个 Codex 主会话的可见文字和执行进展。每个 agent 有独立的持久 Codex thread、聊天记录、草稿和回复队列，可以同时回复；切换页面不会停止其他 agent。名称用于区分，关注方向可选，不需要重复介绍任务。已有讨论自动作为默认 agent 保留。服务的模型和主动提问配置适用于所有 agent。

讨论 agent 默认在主 Codex session 的工作目录中运行，按问题读取相关文档、源码、Git 状态和未提交差异。工作目录和近期修改文件的路径随上下文同步；文件内容按需读取，不依赖主 agent 把正文复制到聊天里。追问文件变化时会重新读取当前版本。网页「会话信息」中显示工作目录。

实际仓库位于其他目录时，关联或重新关联时指定 `--workspace`。该目录应用于同一 session 下的所有讨论 agent，已有聊天与 Codex thread 保留；省略参数会保留已有的显式目录。

```bash
talkwithagent attach --main-thread <Codex-任务-ID> --workspace /absolute/path/to/repository
```

```bash
talkwithagent attach --main-thread <Codex-任务-ID> --title "当前任务名称"
talkwithagent publish --session <讨论-session-ID> "已完成接口分析，正在实现页面"
talkwithagent discussion --session <讨论-session-ID>
talkwithagent pending --session <讨论-session-ID>
talkwithagent ack --session <讨论-session-ID> <消息-ID>
talkwithagent publish --session <讨论-session-ID> "任务完成" --status completed
```

如果运行环境提供 `CODEX_THREAD_ID`，`attach` 可以省略 `--main-thread`。网页 URL 的 `session=` 是讨论记录 ID，并非 Codex task/thread ID。

服务约每 5 秒通过 [Codex App Server 的 thread/read](https://learn.chatgpt.com/docs/app-server#read-a-stored-thread-without-resuming) 读取绑定会话，无需手动复制上下文。读取失败时保留记录、显示错误并等待恢复，避免在缺少主会话信息时回答。网页链接明确绑定一项任务；没有 session 参数时提示从 Codex 关联，不自动切到其他任务。

agent 根据新进展判断是否需要提出问题和建议，直接插入连续对话。相同进展不重复触发，完成通知不触发追问。输入框始终可用，消息和草稿属于整个会话，思考期间可以继续输入。旧话题中的非归档消息仍保留在对话中。

主 agent 用 `discussion` 读取该 session 下所有 agent 的网页讨论，用 `pending` 读取它们已明确提交的执行反馈，输出带有 `agent_id` 和 `agent_name`。仍只需保存一个 session ID；`ack` 可统一确认各个 agent 的反馈。普通聊天不等于执行授权。卡片回答、采纳结果和明确提交的结论进入反馈队列；执行端确认读取后显示“Codex 已读取”。`ack` 只表示已读取，不表示已经执行。

## 集成边界

主会话到讨论 agent 的同步是自动轮询已持久化的可见文字及文件定位信息，不是实时订阅桌面内存，也不包含隐藏推理、工具输出原文或图片。文字提供最近最多 120,000 字符，超出时明确标记截断；另提供最近 20 次文件修改的路径和 10 个命令工作目录。定位信息只说明最近处理过哪些材料，实际内容和差异由讨论 agent 读取本机文件确认。`publish` 用于补充执行摘要和声明 working / waiting / completed；不再是讨论上下文的唯一来源。

网页到主 agent 的读取仍由插件在执行检查点完成，不会在 Codex 任务结束后自动唤醒执行端。数据只读取绑定的 task ID；讨论 agent 只接收该任务的上下文。

讨论 agent 使用独立且持久化的 Codex thread，通过 [Codex CLI 的工作目录与 sandbox 配置](https://learn.chatgpt.com/docs/developer-commands?surface=cli) 启动或恢复到当前任务目录。保留只读 sandbox、禁用审批升级和网页搜索；提示词允许读取任务材料，把修改文件、构建、测试和执行任务留给主 agent。工作目录是定位入口，并不是额外实现的文件读取隔离边界。此能力仅覆盖服务所在机器可访问的本地文件，不会自动获得云文档或远端工作区的权限。

讨论 agent 不是桌面任务树中的原生子任务。只有执行端上报 `waiting` 才能生成决策话题，真正暂停与恢复依赖执行 agent 配合。

服务使用本机 Codex 账户额度。服务只对本机开放，校验 Host、Origin 和自定义请求头，没有多用户认证，不应暴露到公网。会话数据库包含任务内容，不放进发布包。

## 开发与打包

```bash
npm test
python3 check_package.py
npm pack
# 发布者登录 npm 后，可从本目录发布：
npm publish --access public
```

`npm pack` 生成可分发的 `talkwithagent-0.4.0.tgz`。它包含命令、服务、网页、配置读取代码和 Codex 插件，不包含本地会话数据库。可以通过 `npm install -g /path/to/talkwithagent-0.4.0.tgz` 安装。

`npm test` 检查多个 agent 的共享主上下文、独立聊天、并发队列、持久化、反馈汇总、重试、只读调用参数、工作目录选择和文件定位信息过滤。`check_package.py` 在临时目录实际打包、安装和启动服务，验证命令、创建/切换 agent、工作目录绑定、跨 session 隔离及静态资源，不调用模型。模型与浏览器的交互验证请使用独立数据目录。
