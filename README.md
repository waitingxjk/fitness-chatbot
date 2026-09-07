# fitness-bot

为 Flutter iOS 应用「练伴」提供动作搜索、推荐和文本问答的后台服务。
Python **3.12**，**uv** 管理依赖，**LangGraph** 编排 agent，**FastAPI** 提供普通 HTTP 接口。
聊天返回 **NDJSON 流**，iOS 直接请求 `/v1/chat`，无 MCP 握手或 `/mcp` 路由。

## 启动

```bash
uv sync --locked
uv run python scripts/setup_mysql.py
docker compose --env-file .env.mysql up -d --wait mysql
# 配置已有 .env；不存在时可从 .env.example 复制。
uv run fitness-bot
# 等价：uv run python -m fitness_bot
# 开发热更新：
uv run uvicorn fitness_bot.app:create_app --factory --reload
```

默认监听 `127.0.0.1:8000`，接口文档为 [Swagger UI](http://127.0.0.1:8000/docs)。
真机局域网测试时使用 `HOST=0.0.0.0 uv run fitness-bot`，App 请求开发机的局域网 IP。

`.env` 配置 `OPENAI_API_KEY`、`OPENAI_MODEL`，以及可选的 `OPENAI_BASE_URL`。
兼容已有 `OPENAI_API_BASE`，两者同时存在时 `OPENAI_BASE_URL` 优先。
默认模型为 `gpt-4o-mini`，请设为所用供应商实际支持的模型；需要支持工具调用和流式输出。
支持推理的模型可通过 `OPENAI_REASONING_EFFORT` 配置强度；默认不发送此参数。
快速实测示例：`OPENAI_MODEL=gpt-5.4-mini OPENAI_REASONING_EFFORT=low uv run fitness-bot`。
没有 Key 时目录和健康检查仍可用，聊天返回 HTTP 503；不会伪装成已调用真实模型。
现有 `.env` 保留，不进入 Git。

```bash
curl http://127.0.0.1:8000/health
curl -N http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"找两个哑铃胸部动作"}'
uv run python scripts/chat.py '找两个哑铃胸部动作'
```

## Web 测试界面

启动服务后打开 [练伴 Playground](http://127.0.0.1:8000/playground/)。
页面随 Python 包发布，与 API 同源，无需 Node、前端构建或独立端口。
页面右上角显示连接状态，可重新连接；测试另一实例时打开其 `/playground/`。

- 输入框支持直接粘贴剪贴板图片（⌘V / Ctrl+V），也可通过「＋ 图片」选择并上传 JPEG / PNG / WebP，每条最多 3 张，每张 5 MiB、2000 万像素。
  支持纯图片或图文一起发送，可预览、移除和重试上传；暂不支持 HEIC、GIF 或动画图片。
  模型需要支持视觉输入；图片与文字一起传给请求理解和回复模型，原有 NDJSON 回复不变。
  原文件存放 `runtime/uploads/`（可通过 IMAGE_DIRECTORY 指定），MySQL 的 `chat_turn_media`
  表保存轮次关联。备份会话时须同时备份此目录和数据库。未发送的上传文件也保留在本地。
  历史显示全部图片；模型在最近 15 个成功轮次中最多读取最近 6 张历史图片，外加本轮图片。
  图片内容会发送给当前配置的模型供应商进行分析；浏览器不直接连接供应商。
- 助手文字支持 Markdown：加粗、多级列表、标题、表格、引用、代码块与任务列表。
  流式增量和历史使用同一渲染器；复制仍保留原始 Markdown。HTML 按文本显示，远程图片按链接显示。
- 中间持续对话：Enter 发送、Shift + Enter 换行；可停止生成并保留部分文本。
- 左侧新建/选择历史对话：新对话在第一条请求时入库，历史及轮次按需分页读取。
  浏览器只记住选中会话的 ID；文字、卡片和训练上下文以服务端数据为准。
- 搜索和推荐动作卡可打开完整动作步骤；测试台保留每轮卡片方便比对，不修改训练。
- 右侧处理过程展示请求分类、检索条件、命中数量/ID、回答阶段，并随成功回复写入 MySQL。
  这些是程序执行摘要，不是模型内部思维链。旧记录或失败轮次可能没有保存的过程摘要。
- 原始事件面板显示 status / trace / text_delta / exercise_cards / done / error 和未知事件；
  本页只保留每轮最近 500 条，历史提供最终 response 快照，不伪造原始事件。
- 上下文面板可编辑当前训练、最近动作和可用器械 JSON；切换历史恢复最后一轮的快照。

服务端仍使用 `.env` 的模型配置，浏览器不接触供应商密钥。停止/断流后可再次发送问题，
使用新的 request_id；后端可能需要短暂时间清理上一轮。如遇 409，稍后重试。

## 内容和流程

`理解请求 → 本地目录检索 → 流式回复`，三个节点运行于真实 LangGraph。
模型提取意图和结构化检索条件；服务端过滤本地数据并生成可信动作卡；模型根据结果流式回答。
当前检索是名称、别名及部位/器械/肌群过滤，不依赖向量库或外部动作 API。

- 本地保存 iOS 全部 **1,324 条中文动作**、术语、步骤、目录版本及校验和。
- 保留 iOS 动作 ID、`mediaId` 和动作记录 camelCase 字段，App 可复用已有图片与详情页。
- 搜索最多 2 张卡，推荐最多 3 张卡；无匹配不补充无关动作。
- 后端从 MySQL 恢复历史消息和上一组动作 ID；客户端只需带 conversation_id 和本轮训练快照。
  追问阶段也会传入最近一组卡片的完整动作资料；中间的无卡片回复不会清空这组引用。
- 推荐排除当前训练已有动作，并按已提供器械过滤；历史不足时明确说明。
- 服务端不创建或修改训练。添加按钮仅是 UI 提示，iOS 点击时仍须重新验证训练状态。

接口与完整事件协议见 [docs/api.md](docs/api.md)。

## 目录同步

完整动作库就在 **chatbot 本地**：

- [完整 1,324 条动作](src/fitness_bot/data/exercises_zh_hans.json)
- [中文术语](src/fitness_bot/data/catalog_terms_zh_hans.json)
- [版本与完整性清单](src/fitness_bot/data/manifest.json)

这些文件是独立副本，随 Python 包发布；不会在服务运行时读取 iOS 项目。

```bash
uv run python scripts/sync_catalog.py --source ../flutter-init
```

知识库位于 `src/fitness_bot/data/`，作为包数据进入构建产物，运行时无需相邻 iOS 仓库。
`manifest.json` 记录上游版本、数量和 SHA256。启动时校验完整性。
只保存媒体 ID，不复制或托管 JPG/GIF；数据来源和许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 聊天存储

Docker MySQL 8.4 监听 `127.0.0.1:13306`，数据库和用户均为 `fitness_bot`。
随机密码及 DATABASE_URL 位于仅本机可读的 `.env.mysql`，不会覆盖原有 `.env` 或进入 Git。
配置读取顺序为 `.env`、`.env.mysql`、进程环境变量（后者优先）。
`fitness-bot_mysql_data` 持久卷保存数据；容器重启不丢失。

启动时幂等创建 `conversations` 和 `chat_turns` 表，保存用户文本、训练快照、助手文本、
完整动作卡、生成状态及错误码。成功写库后才发送 done；失败/取消保留已接收到的部分文字。
进程被强制终止时，尚未写库的文本增量可能丢失，残留生成锁在超时后由下次请求回收。
没有配置 DATABASE_URL 或数据库不可用时，服务启动失败；不会静默切换到内存存储。

同一 conversation_id 同时只允许一轮生成；同一 request_id 的已完成请求返回存储结果，
不重复调用模型。生成中或失败请求重复调用返回 409，失败重试需使用新 request_id。

```bash
docker compose --env-file .env.mysql ps
curl http://127.0.0.1:8000/v1/conversations
# 用实际 conversation_id 替换下面的 UUID：
curl http://127.0.0.1:8000/v1/conversations/00000000-0000-0000-0000-000000000000/turns
```

## 全量 prompt 评估

全量 1,324 条「ID + 名称 + 部位/器械/主要肌群」为 **36,634 tokens**；
加全部动作步骤为 **160,393 tokens**（按行格式）。
详见 [评估报告](docs/catalog-prompt-evaluation.md)。当前暂缓向量 RAG，服务仍使用已验证的目录检索，
尚未切换为全量 prompt。

```bash
uv run python scripts/count_catalog_tokens.py --output docs/catalog-token-counts.json
```

## 验证

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
# 仅开发验证需要 Node 18+；日常运行无需 Node
node --test tests/web/*.test.mjs
uv build
```

测试注入模型替身，验证实际 LangGraph、完整知识库和 HTTP 协议，不使用真实 API Key。
真实供应商连通性需配置模型后用上述聊天命令检查。

## 当前边界

这是可运行的开发服务骨架。对话在 MySQL 持久化；生成上下文最多取最近 15 个成功轮次，
单条文本截取前 4,000 字符，完整原文仍保留在数据库。服务不提供断流续传或训练事实写入。
尚未修改 iOS 的本地练伴实现。
账户鉴权、限流、云部署和模型回答质量评估不包含在本次初始化；默认仅监听本机。
