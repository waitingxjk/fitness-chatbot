# fitness-bot

为 Flutter iOS 应用「练伴」提供动作搜索、推荐和文本问答的后台服务。
Python **3.12**，**uv** 管理依赖，**LangGraph** 编排 agent，**FastAPI** 提供普通 HTTP 接口。
聊天返回 **NDJSON 流**，iOS 直接请求 `/v1/chat`，无 MCP 握手或 `/mcp` 路由。

## 启动

```bash
uv sync --locked
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
没有 Key 时目录和健康检查仍可用，聊天返回 HTTP 503；不会伪装成已调用真实模型。
现有 `.env` 保留，不进入 Git。

```bash
curl http://127.0.0.1:8000/health
curl -N http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"找两个哑铃胸部动作"}'
uv run python scripts/chat.py '找两个哑铃胸部动作'
```

## 内容和流程

`理解请求 → 本地目录检索 → 流式回复`，三个节点运行于真实 LangGraph。
模型提取意图和结构化检索条件；服务端过滤本地数据并生成可信动作卡；模型根据结果流式回答。
当前检索是名称、别名及部位/器械/肌群过滤，不依赖向量库或外部动作 API。

- 本地保存 iOS 全部 **1,324 条中文动作**、术语、步骤、目录版本及校验和。
- 保留 iOS 动作 ID、`mediaId` 和动作记录 camelCase 字段，App 可复用已有图片与详情页。
- 搜索最多 2 张卡，推荐最多 3 张卡；无匹配不补充无关动作。
- 每次请求可携带历史消息、上一组动作 ID、当前训练及近期训练快照。
- 推荐排除当前训练已有动作，并按已提供器械过滤；历史不足时明确说明。
- 服务端不创建或修改训练。添加按钮仅是 UI 提示，iOS 点击时仍须重新验证训练状态。

接口与完整事件协议见 [docs/api.md](docs/api.md)。

## 目录同步

```bash
uv run python scripts/sync_catalog.py --source ../flutter-init
```

知识库位于 `src/fitness_bot/data/`，作为包数据进入构建产物，运行时无需相邻 iOS 仓库。
`manifest.json` 记录上游版本、数量和 SHA256。启动时校验完整性。
只保存媒体 ID，不复制或托管 JPG/GIF；数据来源和许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 验证

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

测试注入模型替身，验证实际 LangGraph、完整知识库和 HTTP 协议，不使用真实 API Key。
真实供应商连通性需配置模型后用上述聊天命令检查。

## 当前边界

这是可运行的开发服务骨架。对话状态由 iOS 按请求传入，`conversation_id` 只用于关联；
服务端不持久化聊天或训练数据，也不支持断流续传。尚未修改 iOS 的本地练伴实现。
账户鉴权、限流、云部署和模型回答质量评估不包含在本次初始化；默认仅监听本机。
