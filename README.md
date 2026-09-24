# TalkWithAgent

任务执行留在 Codex，讨论在网页中。你和 agent 都可以发起话题，在各自话题里交流，再把确定的结论交给执行端。

## 安装

需要 Node.js 18+、Python 3.10+，以及已安装并登录的 Codex CLI（支持 `exec resume --output-schema`）。npm 包没有第三方运行时依赖，使用本机 Codex 登录。

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
| `auto_discuss` | 是否主动创建话题；关闭后仍可回复用户和在已有话题内追问 |
| `max_open_topics` | agent 同时等待回应的话题上限，1–20 |
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

每个 Codex 任务关联一份持久讨论；重复关联会返回同一个 session 和网页链接。

```bash
talkwithagent attach --main-thread <Codex-任务-ID> --title "当前任务名称"
talkwithagent publish --session <讨论-session-ID> "已完成接口分析，正在实现页面"
talkwithagent pending --session <讨论-session-ID>
talkwithagent ack --session <讨论-session-ID> <消息-ID>
talkwithagent publish --session <讨论-session-ID> "任务完成" --status completed
```

如果运行环境提供 `CODEX_THREAD_ID`，`attach` 可以省略 `--main-thread`。网页 URL 的 `session=` 是讨论记录 ID，并非 Codex task/thread ID。

agent 根据任务进展判断是否存在具体、值得回应的问题；相同进展不重复触发，完成通知不触发新话题。双方的话题共同显示，各自保留消息和草稿。思考期间可以继续输入，消息会持久化排队。

普通聊天不进入执行队列。“采纳 / 不采纳”和明确提交的结论才回传；执行端确认读取后显示“Codex 已读取”。`ack` 只表示已读取，不表示已经执行。

## 集成边界

同步由插件流程在检查点调用命令完成，不是自动订阅完整桌面历史。插件不会在 Codex 任务结束后自动唤醒执行端。`--main-thread` 只是关联标识。

讨论 agent 使用独立且持久化的 Codex thread，调用只读 sandbox；它不是桌面任务树中的原生子任务。只有执行端上报 `waiting` 才能生成决策话题，真正暂停与恢复依赖执行 agent 配合。

服务使用本机 Codex 账户额度。服务只对本机开放，校验 Host、Origin 和自定义请求头，没有多用户认证，不应暴露到公网。会话数据库包含任务内容，不放进发布包。

## 开发与打包

```bash
npm test
python3 check_package.py
npm pack
# 发布者登录 npm 后，可从本目录发布：
npm publish --access public
```

`npm pack` 生成可分发的 `talkwithagent-0.1.0.tgz`。它包含命令、服务、网页、配置读取代码和 Codex 插件，不包含本地会话数据库。可以通过 `npm install -g /path/to/talkwithagent-0.1.0.tgz` 安装。

`npm test` 检查话题、队列、配置优先级、关闭主动提问、多任务隔离和模型参数传递。`check_package.py` 在临时目录实际打包、安装和启动服务，验证命令及静态资源，不调用模型。模型与浏览器的交互验证请使用独立数据目录。
