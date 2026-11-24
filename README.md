# fitness-bot

为 Flutter iOS 应用「练伴」提供动作搜索、训练复盘、整次训练方案和文本问答的后台服务。
Python **3.12**，**uv** 管理依赖，**LangGraph** 编排 agent，**FastAPI** 提供普通 HTTP 接口。
聊天返回 **NDJSON 流**，iOS 直接请求 `/v1/chat`，无 MCP 握手或 `/mcp` 路由。

App 接入请先读 [Chatbot 能力与输出接口交接](docs/handoff/chatbot-app-handoff.md)，
内含流式状态机、跨端图表、动作草稿/计划保存边界及可直接用于渲染测试的 JSON 样例。

复盘与整次训练推荐的设计资料见 [训练方法知识库](docs/knowledge/training/README.md)。
运行时已接入带版本的研究原则与产品规则子集。Boyle 正文阅读暂缓，未将未读内容作为依据。
具体剂量仍是候选方案；尚未验证的恢复评分、精确疲劳矩阵和历史负重口径不由模型补造。

专家方法组织为三个独立的运行时 skill：[训练复盘](src/fitness_bot/skills/training-review/SKILL.md) 与
[训练推荐](src/fitness_bot/skills/training-recommendation/SKILL.md)，以及
[自定义动作建档](src/fitness_bot/skills/exercise-authoring/SKILL.md)。复盘解释记录事实；推荐依据服务端统计、
目标与当前条件生成动作备选、计划预览或完整/修订方案。推荐无需额外调用模型生成复盘报告。
方法文件作为 Python 包资源按固定名称加载；目录约束、数值计算、权限和持久化仍由程序负责。
复盘先编排内容结构和图表位置，再显示统计范围，并按块流式呈现“核心判断 + 按实际发现组织的主题分析”，支持自然段、列表和小标题。
准确数值由服务端事实引用填入正文，无效证据引用只影响对应结论。网页端展示训练时间线、每次完成组数和
主部位分布图，按编排顺序穿插在正文中，支持点选数值与展开数据表；详细依据和数据说明默认折叠。
新增跨端协议 `schema_version / outline / blocks / artifacts / datasets`，正文只引用图表 ID，
图表格式和 JSON 数据分别传输，无 HTML/WebView 依赖。接入说明见 [接口文档](docs/api.md#ordered-report-composition-v1)。
计划与预览可先流式显示标记为“生成中”的构思，完成后替换为最终结果；完整处方在校验并保存后才确认。
咨询追问与搜索保留基础处理流程；返回形式包括文本、动作卡、结构化复盘、计划预览和完整计划。

在 `/playground/` 可直接讨论推荐思路、分析历史或提出训练需求，无需先填表。
右上角「目标与状态」可保存目标、优先动作、器械、可用时间及当前反馈。
只有生成具体量化方案时才检查必要条件；资料不足时先结合已有记录讨论，不重复固定追问。
复盘支持单次和多次：单次按记录序号查看动作、逐组变化、部位重叠和时长；多次默认取最近 7 次已完成训练，
不足 7 次就使用已有记录，不受最近一周是否有训练影响。明确请求“一周／一个月”等日期范围时才按日期窗口统计。
补充训练开始间隔、同部位再练间隔及用户确认的周期；通用数据限制收在可展开的说明中。未记录的实际休息时间保持未知，录入间隔不作为休息；
序号重复、证据截断和周期未知会明确标记。“复盘上次训练”选择最近一次已完成训练，也可通过 API 指定训练。
方案包含动作顺序、组次、休息、选重依据和用时预算；
可采用、修改、跳过并记录原因，或关联已经由 iOS 上传的实际训练。刷新后状态保留。
右上角「确认记录」可逐条补充负重口径、器械与动作条件及最后工作组余力；确认可撤回，
且不会修改原始训练。只有满足证据条件的动作才进入小幅加量候选。新接口与输入限制见 [API 文档](docs/api.md#training-decisions-v1-prototype)。

自动验证使用合成数据和模型替身；`uv run python scripts/training_decision_test_server.py`
可启动独立的离线 UI 测试页（18082 端口、临时数据库），不调用真实模型。

## 自定义动作

在聊天中说“帮我创建墙天使”，`exercise-authoring` skill 会查重、补全动作元信息；有关键歧义时先追问。
聊天只返回带头像的草稿，不保存动作。用户查看后点击“确认保存”，前端再调用独立接口写入个人库。
草稿与保存状态随聊天历史保留；重复确认不会重复创建。保存后可搜索、参与推荐和复盘，在动作详情中编辑或归档。
默认头像为按训练类型分配的内置 SVG 图标；接口同时返回可供 Flutter 原生映射的图标 key。
个人动作按账号隔离，标准库保持原样；不会自动改写旧训练记录。接口见 [Personal exercises](docs/api.md#personal-exercises-and-authoring-drafts)。

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
默认模型为 `gpt-4o-mini`，请设为所用供应商实际支持的模型或部署名称；需要支持工具调用和流式输出。
若供应商示例使用 `client.responses.create`，设置 `OPENAI_USE_RESPONSES_API=true`，
并完整保留部署名后缀，例如本地 `.env` 示例中的 `gpt-5.4-mini-1`。默认使用 Chat Completions。
支持推理的模型可通过 `OPENAI_REASONING_EFFORT` 配置强度；默认不发送此参数。
运行 `uv run fitness-bot` 会读取 `.env`；避免用启动命令覆盖已验证的部署名称和接口配置。
没有 Key 时目录和健康检查仍可用，聊天返回 HTTP 503；不会伪装成已调用真实模型。
现有 `.env` 保留，不进入 Git。

```bash
curl http://127.0.0.1:8000/health
curl -N http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"找两个哑铃胸部动作"}'
uv run python scripts/chat.py '找两个哑铃胸部动作'
```

## 用户身份与鉴权

用户的内部 ID 由服务器在创建时生成：`uuid4().hex`，32 位小写十六进制。`user` 表保存
`id, display_name, created_at, updated_at`；同名用户也有不同内部 ID。
客户端首次连接发送 `POST /v1/users/bootstrap`，请求为
`{"installationId":"安装持久化标识","displayName":"可选显示名"}`，返回
`{"userId":"32位内部ID","displayName":"可选显示名或null","authEnabled":false}`。
客户端持久保存返回的 ID，此后通过 `X-User-Id` 发送。相同安装重试、丢失响应或重启都复用原用户。
新的安装创建独立用户；重试不会修改既有显示名。

所有 `/v1/*` 接口统一解析身份，训练、对话和图片始终按内部用户 ID 隔离。
默认 `AUTH_ENABLED=false`：bootstrap 可按安装创建用户，其他请求只能使用已存在的内部 ID
或已迁移的旧别名。未知 ID 返回 404，不会因为任意请求头自动创建用户；非法格式返回 422。
旧版无用户头请求通过 `DEFAULT_USER_ID=local-user` 查找一次性迁移的默认用户。
此模式仅适用于受信任环境，知道已有用户 ID 的请求者仍可声明该身份。

启用 `AUTH_ENABLED=true` 后，包括 bootstrap 在内的每次 `/v1/*` 请求必须带
`Authorization: Bearer <用户token>`。缺失、无效、过期或已撤销 token 返回 401。
bootstrap 仅将安装绑定到 token 用户；不会匿名创建用户。安装已绑定他人或历史来源归属有歧义
返回 409，不合并账户。可选 X-User-Id 必须解析为同一 token 用户，否则 403。
`GET /v1/me` 返回当前内部 userId、displayName 和 authEnabled。
`/health` 和 `/playground/` 静态资源始终公开，图片 API 仍需身份请求头。

管理员在服务器本地创建用户、签发或撤销 token：

```bash
uv run python scripts/manage_tokens.py create-user --display-name 小明
uv run python scripts/manage_tokens.py issue --user-id <返回的内部userId> --expires-in-days 30
# JSON 输出中的 token 是仅此次展示的秘密；安全交给对应用户。tokenId 用于撤销。
uv run python scripts/manage_tokens.py revoke --token-id <签发输出的tokenId>
```

`issue` 只能为已存在用户签发，也兼容已迁移的旧别名查询，不自动创建别名账户。
省略 `--expires-in-days` 表示不过期。token 使用 32 字节密码学随机值，活动表
`user_access_tokens` 只保存 SHA256 摘要、归属、过期/撤销时间；撤销在下一次请求生效，
已开始的流可以完成。远程使用应通过 HTTPS 传输 token。

启动自动执行幂等迁移。旧单用户数据先获得明确归属；随后通过 `legacy_user_aliases`
将旧用户映射到随机内部 ID，在同一事务中保留 token ID/摘要/过期/撤销状态并更新全部训练、
会话、图片归属。旧 `users`、`user_tokens` 和 `workout_snapshots` 保留供核对。
已有 sourceId 恰好对应一个旧用户时，使用同名 installationId 的 bootstrap 会保持原账号；
多个归属返回 409。后续修改默认配置不会重新分配数据或收养新孤立文件。
备份应包含数据库与 IMAGE_DIRECTORY。升级后必须停止旧版服务；旧表不再双向同步，
直接回退旧服务无法包含新版写入，也无法反映新版撤销的 token。

## Web 测试界面

启动服务后打开 [练伴 Playground](http://127.0.0.1:8000/playground/)。
页面随 Python 包发布，与 API 同源，无需 Node、前端构建或独立端口。
页面右上角显示连接状态。开发模式可填写昵称并点击「创建新用户」，服务器生成内部 ID，刷新页面仍使用同一用户。也可配置已有内部 ID（或迁移别名）和 Bearer 令牌后点击「应用身份并连接」；成功后显示规范的内部 ID 和昵称。
创建请求失败后重试会复用同一初始化标识，避免重复建户。身份解析成功前不能上传图片或请求用户数据。
用户 ID 会保留，令牌仅在当前页面内存中，刷新后需重新填写。切换身份会清空当前页面的旧用户内容，历史选择按用户单独保存；图片通过携带身份的请求加载。测试另一实例时打开其 `/playground/`。

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

`理解请求 → 准备资料/检索 → 编排 → 分块生成`，四个节点运行于真实 LangGraph。
复盘使用编排模型选择文字主题与图表，程序校验大纲、分配 ID 并绑定事实数据；最后一次模型调用只填充
冻结大纲中的文字块。其它意图目前透传编排节点，保持已有文本/动作卡/计划输出。
模型提取意图和结构化检索条件；服务端过滤本地数据并生成可信动作卡；模型根据结果流式回答。
当前检索是名称、别名及部位/器械/肌群过滤，不依赖向量库或外部动作 API。

- 本地保存 iOS 全部 **1,324 条中文动作**、术语、步骤、目录版本及校验和。
- 保留 iOS 动作 ID、`mediaId` 和动作记录 camelCase 字段，App 可复用已有图片与详情页。
- 搜索最多 2 张卡，推荐最多 3 张卡；无匹配不补充无关动作。
- 后端从 MySQL 恢复历史消息和上一组动作 ID；客户端只需带 conversation_id 和本轮训练快照。
- 聊天同时读取当前用户在 MySQL 中已同步、已完成的训练，按训练开始日期取最近 30 次，
  跨该用户的安装来源汇总，排除删除记录、草稿动作和未完成组。保留动作名称、日期、
  各组次数、负重类型与原单位；不发送任意旧版 JSON 扩展字段。摘要随聊天发送给已配置的
  模型供应商，并随轮次保存，处理过程显示读取的训练数量。单次训练最多 100 条动作、
  每条最多 50 个已完成组，总摘要最多 100,000 个紧凑 JSON 字符；截断时明确标记范围有限。
  `recent_exercises` 或此前动作卡为空，不再被当作账号没有训练记录。
  追问阶段也会传入最近一组卡片的完整动作资料；中间的无卡片回复不会清空这组引用。
- 推荐排除当前训练已有动作，并按已提供器械过滤；历史不足时明确说明。
- 聊天模型不创建或修改训练。添加按钮仅是 UI 提示，iOS 点击时仍须重新验证训练状态。

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

## iOS 训练备份

`PUT /v1/workouts/{workout_id}` 异步备份 iOS 每次本地保存后的完整训练快照，支持历史回填、
训练/动作/组删除，以及删除标记。复用现有 MySQL，启动时新增 `user_workout_snapshots` 表；
按 `(userId, sourceId, workoutId)` 保存单调版本，更高版本原子替换，同版本相同内容幂等，
较旧版本或同版本不同内容返回 409。数据库提交成功后才返回版本和 uploadedAt 确认。

`GET /v1/workouts?sourceId=...` 和 `GET /v1/workouts/{workout_id}?sourceId=...`
可查询已备份快照和删除标记。草稿及旧版原始 JSON 字段会保留，不调用模型或改变训练编辑规则。
sourceId 只是来源标识；账户归属由统一身份解析确定，确认响应包含 userId。
请求样例在 `tests/fixtures/workout_upload.json`，完整字段和重试语义见 [API 文档](docs/api.md#ios-训练备份)。

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
单条文本截取前 4,000 字符，完整原文仍保留在数据库。服务不提供断流续传。
iOS 训练备份接口无需模型即可使用；聊天只读已同步训练，训练内容仍由客户端编辑，
服务端不自动执行训练操作。未同步、未结束或已删除的训练不进入历史分析摘要。
已支持每用户 token 鉴权，默认关闭；限流、云部署尚未实现；已有少量合成人工模型样例，尚未完成大规模回答质量评估，默认仅监听本机。

### Structured suggestions and cards

The playground renders single/alternative exercise suggestions and ordered whole-session plans using the same per-set cards. Expand a card to inspect or edit warmup/working type, reps/time, load convention, rest and target effort. Whole-plan edits save an immutable new version before confirmation; reopening the original conversation follows its newest version. Single-card web edits remain page-local; adding to a live workout is performed by the Flutter app.

When a session request lacks necessary conditions, it returns ordered catalog candidate cards marked as incomplete. These cards contain no prescribed sets or load and cannot be added, accepted or started. The page shows the next missing condition and a shortcut to complete the profile. Existing deferred text-only replies explain that no cards were generated and offer a new card request; their saved content is retained.

Confirmed session plans appear in the connected Flutter app as pending. Starting explicitly creates incomplete rows in order; confirmation itself does not create a workout. Existing active training is preserved. See `docs/api.md` for prescription and revision contracts.

Optional `EXERCISE_MEDIA_DIRECTORY` points to local catalog thumbnails. The development default uses the adjacent Flutter repository's `assets/exercises/images`; deployments without those files show text cards without thumbnails.
