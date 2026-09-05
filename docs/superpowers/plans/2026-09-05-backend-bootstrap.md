# 练伴后端初始化

目标：在 fitness-bot 独立 Git 仓库中实现 Python 3.12 / uv 服务。

设计：FastAPI 提供 POST /v1/chat NDJSON 流式接口；LangGraph 顺序执行
理解请求、查询本地动作库、生成流式回答。聊天历史和训练状态由客户端逐次提供，
不使用共享的服务端会话记忆。动作卡只从真实目录生成，服务端不写训练记录。

完整复制 iOS 的 1,324 条中文动作及术语 JSON，记录版本和 SHA256，保留许可。
第一版使用字段过滤和名称检索，避免引入外部数据库及 embedding 服务。
模型通过 OpenAI-compatible API 接入，可配置 base URL 和模型。

执行与验证：

- [x] 初始化 Git、pyproject、Python 版本、依赖锁和忽略规则。
- [x] 测试完整目录数量、ID、中文别名、组合条件和无结果，再实现 catalog.py。
- [x] 测试 LangGraph 文本增量、搜索 2 卡/推荐 3 卡、活动训练按钮，再实现 agent.py。
- [x] 测试HTTP NDJSON 流式调用、结构化结果和错误状态，再实现 app.py。
- [x] 编写 uv run 客户端示例、协议文档、导入脚本，执行 lint、pytest 和构建验证。

协议决定：用户确认不使用 MCP，iOS 直接 HTTP 调用。POST /v1/chat 返回
application/x-ndjson，每行一个 JSON 事件；支持 text_delta、exercise_cards、done、error。
最终 done 事件包含完整、权威结果，异常流以 error 终止而不发送 done。

边界：初始化包含真实模型适配器，但自动测试不调用真实模型、不读取真实密钥。
iOS 接入、账户鉴权、持久化聊天及云部署后续单独完成。

验证结果：17 项 pytest 测试通过（包含真实 localhost TCP 流式传输）；
ruff lint/format 通过；uv build 生成 wheel 和 sdist；wheel 包含全部 1,324 条数据和许可。
直接以 uv run fitness-bot 启动并检查 /health 成功。存在一条第三方 Starlette/AnyIO
弃用提示，不影响测试。未调用真实模型 API。
