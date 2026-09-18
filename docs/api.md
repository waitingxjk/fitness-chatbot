# HTTP API v1

## 统一身份

服务器创建用户时仅生成一次 `uuid4().hex`（32 位小写十六进制）作为内部 userId；
显示名独立存储，可为空且不要求唯一。训练、聊天、图片和 token 归属均使用内部 ID。

`AUTH_ENABLED=false` 为默认值。首次连接调用 bootstrap 并保存 userId，之后使用
`X-User-Id: <内部ID>`。未知用户返回 404，不会自动创建；非法 ID/旧别名格式返回 422。
已迁移的旧别名可以查找原用户，但响应和新数据归属始终是内部 ID。
无用户头的旧请求按 `DEFAULT_USER_ID`（默认 local-user）查找已迁移默认账号；配置改为未知值
不会生成新账号。此模式仍允许请求者声明已有身份，仅适用于受信任环境。

`AUTH_ENABLED=true`：所有 `/v1/*`（包括 bootstrap）必须提供 `Authorization: Bearer <token>`。
缺失、非法、过期、已撤销均返回 401（带 `WWW-Authenticate: Bearer`），没有匿名回退。
可选 X-User-Id（或已知旧别名）必须解析到 token 用户，否则 403。
管理员使用 `scripts/manage_tokens.py create-user [--display-name ...]` 创建用户，再用
`issue --user-id <内部ID> [--expires-in-days ...]` 签发、`revoke --token-id ...` 撤销 token。
issue 不会创建未知用户。token 原文仅在管理员签发命令输出一次。

`GET /v1/me` 返回：

```json
{"userId":"c6735859072e4a63abf885cd2be5f319","displayName":"小明","authEnabled":false}
```

`/health`、`/playground/` 与其静态资源公开。其他用户的会话、请求 ID 或图片返回 404；
会话列表和训练列表仅含当前用户数据。图片需带相同请求头获取 blob，再显示 blob URL。
撤销影响后续请求；已打开的生成流可以完成。

## 安装初始化

`POST /v1/users/bootstrap`，Content-Type 为 application/json：

```json
{"installationId":"local-install-001","displayName":"小明"}
```

installationId 是客户端持久化的安装标识，1～191 字符，不含空白或 `/`；与训练 sourceId
使用同一安装标识可在升级时找到旧数据。displayName 可省略或 null，最多 120 字符，
去掉首尾空白后为空时存为 null。禁止额外字段。成功返回 200，结构与 `/v1/me` 相同。

- 鉴权关闭时，未绑定的新安装创建独立用户；相同安装重试、响应丢失后重试、服务器重启和
  并发请求都返回同一 userId，不产生多余账号。重复请求不会修改已存在的显示名。
- 若安装标识对应唯一旧用户的已上传 sourceId，则保持该旧用户迁移后的内部 ID。
  同一 sourceId 有多个归属返回 409，不合并数据。未绑定但已属于非旧账号的来源也返回 409。
- 鉴权开启时，必须带 Bearer；只绑定 token 用户，返回其显示名，不匿名注册。
  安装或历史来源属于他人时返回 409；X-User-Id 与 token 不匹配仍先返回 403。
- 鉴权关闭时 bootstrap 的身份由 installationId 决定，用户头不用于选择或创建账户。
  其他 `/v1/*` 仍统一验证用户身份。

客户端应先完成 bootstrap 并持久化响应 ID，再上传训练或请求历史；切换服务实例时重新
确认安装绑定，不能凭旧服务器返回的随机 ID 在新实例创建账号。

## 路由

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/v1/me` | 当前内部用户 ID、显示名与鉴权开关 |
| POST | `/v1/users/bootstrap` | 幂等绑定安装并返回内部用户身份 |
| GET | `/playground/` | Web 流式聊天测试界面 |
| GET | `/health` | 服务存活、目录数量/版本、聊天配置状态（不探测供应商） |
| GET | `/v1/exercises?q=卧推&equipment=dumbbell&limit=20` | 本地目录检索 |
| GET | `/v1/exercises/{exercise_id}` | 完整动作详情；未知 ID 返回 404 |
| POST | `/v1/images` | 上传一张 JPEG / PNG / WebP 原始文件体，返回图片 ID |
| GET | `/v1/images/{image_id}` | 查看 chatbot 本地图片 |
| POST | `/v1/chat` | 流式聊天 |
| PUT | `/v1/workouts/{workout_id}` | 备份版本化训练快照或删除标记 |
| GET | `/v1/workouts?sourceId=...&limit=50&offset=0` | 列出来源的训练备份 |
| GET | `/v1/workouts/{workout_id}?sourceId=...` | 查询训练备份和版本确认 |
| GET | `/v1/conversations?limit=50&offset=0` | 按最近更新时间列出会话 |
| GET | `/v1/conversations/{conversation_id}/turns?after_id=0&limit=100` | 按 ID 升序查询完整轮次 |

目录过滤参数还有 `body_part`、`target_muscle`，可用英文 code 或术语表中文名。
多个条件取交集；`q` 对中文名称、英文名称、别名作子串匹配。limit 为 1～100。
未提供检索条件时返回空列表。此接口不消耗模型调用。

## 聊天请求

`Content-Type: application/json`。最简请求只需要 `message`；纯图片请求可只提供非空 `images`。

```json
{
  "message": "第二个换成无器械",
  "conversation_id": "d0dc53a0-f613-47bc-a2a1-96b3c2e38f50",
  "request_id": "a5ab9bc5-efbd-466b-8a5a-35591c64b54c",
  "context": {
    "active_workout": {"id": "workout-1", "status": "active", "exercise_ids": []},
    "recent_exercises": [
      {"exercise_id": "0001", "performed_at": "2026-09-04T10:00:00Z", "completed_sets": 3,
       "repetitions": 30, "weight_kg": null, "duration_seconds": null}
    ],
    "available_equipment": ["body weight"]
  }
}
```

- `message` 去除首尾空白后最多 4,000 字符，未提供图片时至少 1 字符；历史最多 30 条。
- `images` 为通过上传接口获得的 ID 列表，最多 3 个。文字与图片不能同时为空。
  成功轮次的图片会进入后续生成上下文；失败/取消轮次的图片仍能在历史中预览。
- 历史角色只接受 `user` / `assistant`。训练快照最多 100 条动作记录。
- `conversation_id` 为 UUID，省略时创建；后续传相同 ID，服务端从 MySQL 恢复最近 15 个成功轮次。
- `history` 和 `previous_result_ids` 作为旧版兼容字段仍通过参数校验，但 HTTP 入口会忽略其值，
  使用已存储的文字和最近 15 个成功轮次中最新一组非空动作卡 ID。
  无卡片的追问轮次不会清空此前卡片引用，回复模型可读取这些动作的完整说明。失败/取消轮次保留供历史查看，不加入生成上下文。
- `request_id` 为可选 UUID，省略时生成。相同 ID、会话及输入的已完成请求会重放单个
  `done` 事件（带 `replayed: true`），包含完整文本和卡片，不会重复生成。
- 相同 request_id 被用于不同输入、请求仍在生成、或会话已有其他正在生成的轮次时返回 409。
  失败后的重试使用新的 request_id；前一次失败和部分回复仍保留。
- `active_workout` 可为 null；状态接受 active、paused、completed、discarded。
- `active_workout`、`recent_exercises` 和器械条件仍代表本轮客户端上下文。
  服务端额外按已解析的用户身份只读 MySQL 已同步、已完成的训练，放入
  `context.workout_history: {total_workouts, workouts, truncated}`。客户端无需填写该字段；
  从聊天历史回传的值会被最新数据库摘要覆盖，不进入请求幂等摘要。已完成请求的重放
  不重新查训练或调用模型，因此训练更新不会破坏重放。
- 训练摘要跨当前用户的来源读取，按 `startedAt` 倒序最多 30 次，排除删除记录、草稿动作、
  未完成组。每次最多 100 条动作，每条最多 50 个已完成组，整个摘要最多 100,000 个
  紧凑 JSON 字符；保留原单位和负重类型，不展开任意 `detailPayload`/`feelingPayload`。
  `total_workouts` 是符合条件的训练总数；`workouts` 是本轮可见窗口，每次包含标题、日期、
  动作总数、动作名称和已完成组明细。全局及单次训练 `truncated` 标记细节被截断，
  动作 `completed_sets` 保留截断前已完成组数。摘要不包含来源标识或其他用户记录。
- 训练摘要会传入模型的请求理解和回复阶段，并在同一事务中保存到聊天轮次 context。
  数据库读取失败返回 503，不伪装成没有训练；不创建该轮聊天、不调用模型。
  回复过程 trace 的 responding 阶段包含 `saved_workout_count`、`saved_workout_total`
  和 `saved_workout_history_truncated`，客户端可显示本轮读取范围。聊天不修改训练数据。

## 图片上传

`POST /v1/images` 的请求体是图片原始字节，Content-Type 为 image/jpeg、image/png 或 image/webp，
不是 multipart/form-data。每张最多 5 MiB、2000 万像素，仅支持静态图片。
服务端验证实际格式和完整性；格式不支持返回 415，超大小返回 413，图片损坏/超像素返回 422。
成功返回 201：

```json
{"id":"64位小写SHA256","url":"/v1/images/64位小写SHA256","mime_type":"image/png","width":800,"height":600,"size_bytes":100000}
```

随后调用聊天：`{"message":"这是什么器械？","images":["上传返回的id"]}`。
仅接受本机已有图片 ID，不接受远程 URL、客户端文件路径或 base64 文本。
聊天记录查询的每个轮次新增 `images: [{id, url}]`，旧轮次返回空列表。
图片保存在 IMAGE_DIRECTORY（默认 runtime/uploads），MySQL 新建 chat_turn_media 表保存关联，
不改变原有表结构。相同内容上传返回相同 ID；每次上传为当前用户单独记录所有权，不会因知道内容哈希获得他人图片。
上传成功但尚未发送的文件也保留。GET 和聊天附件同时检查图片归属。

模型计划/回复均使用 image_url 原生多模态内容块；最多携带本轮 3 张及历史最近 6 张图片。
完整图片仍保留在本地并供历史预览；较早图片从模型上下文省略时会附说明。
更换为不支持视觉的模型时，多模态调用可能返回生成错误；测试页本身不决定模型能力。

## NDJSON 响应

HTTP 200，`Content-Type: application/x-ndjson`，UTF-8，每行一个 JSON 对象，以 `\n` 结束。
文本中的换行由 JSON 转义。每个事件包含 `request_id`、`conversation_id` 和从 1 递增的 `sequence`。

| type | 其他字段 | iOS 行为 |
| --- | --- | --- |
| `status` | `stage`: understanding / retrieving / responding | 显示处理中 |
| `trace` | `step`: stage、title、summary、data | 显示可观测处理过程；不需要时忽略 |
| `text_delta` | `delta`: string | 追加到当前助手文本 |
| `exercise_cards` | `kind`、`result_id`、`cards` | 展示横向动作卡组 |
| `done` | `response`: 完整回复 | 标记成功，用完整回复校准当前轮次 |
| `error` | `code`、`message` | 标记失败，保留输入并提供重试 |

`exercise_cards` 在文本生成成功后发送，无卡片时省略。正常流最后为 `done`；
失败流最后为 `error`，不会随后发送 `done`。网络断开可能没有终止事件，必须视为未完成。
`done.response` 包含 `conversation_id`、`result_id`、`kind`、`text`、`cards`、`catalog_version`、`trace`。
`kind` 是 search / recommendation / chat / review / session_plan / custom_exercise。

`trace` 为附加事件，不改变已有事件含义；客户端应忽略未知事件类型。
`done.response.trace` 是有序阶段列表，随成功轮次写入 response JSON，无需数据库结构迁移。
旧记录可能没有此字段，客户端按空列表处理。失败/取消轮次只保留部分文本，
本页收到的 trace 可即时查看，但不会保存到失败轮次。

```json
{"type":"trace","step":{"stage":"understanding","title":"请求解析完成","summary":"识别为动作搜索","data":{"kind":"search","query":"哑铃卧推","body_part":null,"equipment":null,"target_muscle":null}}}
```

上例省略通用 envelope 字段。understanding 在结构化检索计划完成后发送；retrieving
包含筛选后匹配数量、返回动作 ID 和实际使用的训练器械/排除条件；responding 标记回复开始。
只提供可观测的执行结果，模型隐藏推理不属于接口输出。

动作卡结构（exercise 为目录接口返回的完整动作对象）：

```text
{
  exercise: { id, displayNameZhHans, mediaId, bodyPartCode, equipmentCode, ... },
  equipment_label: string,
  body_part_label: string,
  context: string,
  can_add_to_active_workout: boolean
}
```

聊天外层字段使用 snake_case，内嵌 `exercise` 保留 iOS 目录的 camelCase。
客户端不要把 `exercise_cards` 与 `done.response.cards` 当成两组新结果；它们 result_id 相同。
搜索卡保留；推荐卡在发送下一条消息或添加动作成功时隐藏，这是 iOS 会话 UI 的职责。
隐藏后仍保留动作 ID 和文字，以支持后续引用。点击添加时再次检查当前训练并创建草稿。

参数错误在开始流前返回 HTTP 422；模型未配置返回 503，均为普通 JSON 错误。
数据库不可用时返回 503；会话并发/请求冲突返回 409。
生成过程失败因 HTTP 响应已开始，以流内 error 表达：`timeout` 或 `generation_failed`。
默认整轮超时 90 秒，可通过 `CHAT_TIMEOUT_SECONDS` 调整。
代理需关闭响应缓冲；客户端取消请求会取消相应生成任务。
用户输入在生成前写库，最终助手回复在 done 前提交。失败/取消记录保存已收到的部分文本。
会话查询端点返回完整 response（含动作卡）、context、状态 completed/failed/cancelled/generating、
error_code 及时间（UTC）。after_id 使用上一页最后一条的整数 id；单页最多 100 条。
会话归属在查询、继续对话和请求缓存重放前检查；其他用户的 conversation_id/request_id 返回 404，
不会释放、接管或更新对方的生成锁。

## Swift 按行接收示例

```swift
var request = URLRequest(url: baseURL.appendingPathComponent("v1/chat"))
request.httpMethod = "POST"
request.setValue("application/json", forHTTPHeaderField: "Content-Type")
request.setValue("application/x-ndjson", forHTTPHeaderField: "Accept")
request.httpBody = try JSONSerialization.data(withJSONObject: ["message": "找两个哑铃胸部动作"])
let (bytes, response) = try await URLSession.shared.bytes(for: request)
guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
    throw URLError(.badServerResponse)
}
var finished = false
for try await line in bytes.lines {
    guard !line.isEmpty else { continue }
    let object = try JSONSerialization.jsonObject(with: Data(line.utf8))
    guard let event = object as? [String: Any], let type = event["type"] as? String else {
        throw URLError(.cannotParseResponse)
    }
    // 更新 UI 时切到 MainActor；按 type 追加文字或渲染卡片。
    if type == "error" { throw URLError(.cannotLoadFromNetwork) }
    if type == "done" { finished = true }
}
if !finished { throw URLError(.networkConnectionLost) }
```

Flutter 可使用 `HttpClient`/流式 HTTP 客户端，再经过 UTF-8 解码与 `LineSplitter`；
不要假设网络 chunk 对应一行或一个完整汉字。以上 Swift 示例说明原生 iOS 协议处理方式。

实现参考：[FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/)、
[LangGraph 自定义流](https://docs.langchain.com/oss/python/langgraph/streaming)。

## iOS 训练备份

`PUT /v1/workouts/{workout_id}` 接收完整训练快照或删除标记；与聊天模型相互独立，
没有模型配置时也可用。训练编辑、草稿和有效性判断仍由 iOS 控制，服务端只备份客户端事实。
完整请求样例见 [tests/fixtures/workout_upload.json](../tests/fixtures/workout_upload.json)。

```json
{
  "sourceId": "local-install-001",
  "version": 1,
  "deleted": false,
  "workout": {
    "id": "local-workout-001", "title": "胸部训练", "status": "active",
    "startedAt": "2026-09-07T01:00:00Z", "pausedAt": null,
    "pausedDurationSeconds": 0, "endedAt": null,
    "createdAt": "2026-09-07T01:00:00Z", "updatedAt": "2026-09-07T01:00:00Z",
    "records": []
  }
}
```

所有字段使用 camelCase，样例中 nullable 字段仍必须出现。ID 是非空字符串（最长 191 字符，
不含空白或 `/`），接受现有 `local-*` ID，不要求 UUID。路径 ID 必须与 workout.id 一致。
`sourceId` 是安装实例标识，同名训练在不同来源中互不覆盖；它**不是鉴权凭据**。
用户范围由统一身份解析确定；不同用户使用相同 sourceId/workoutId 互不影响。

- 外层 `version` 和每条 record.version 都是 1～9223372036854775807 的整数；
  外层版本是整个训练聚合的单调版本，每次本地保存（包括删除）递增。
- `workout.status` 接受 `active` / `paused` / `completed`。
  所有时间为带时区的 ISO8601 字符串，保存/读取时规范化为 UTC。
- record 字段为 `id, workoutId, exerciseId, exerciseNameSnapshot, sequence, occurredAt,
  detailType, detailPayload, feelingPayload, isDraft, version, sets`。
  detailType 接受 `weightedReps` / `bodyweightReps` / `duration` / `machineLevelReps`。
  detailPayload 和 feelingPayload 为可空 JSON 对象，保留未知旧字段、嵌套对象和数组。
- set 字段为 `id, exerciseRecordId, sequence, loadType, loadValue, loadUnit, repetitions,
  durationSeconds, difficulty, rpe, isCompleted`。
  loadType 接受 `none` / `external` / `bodyweight` / `added` / `assistance` / `machineLevel`；
  loadUnit 为 null / `kg` / `lb` / `level`；difficulty 为 null / `easy` / `moderate` / `hard`。
  loadValue、repetitions、durationSeconds、rpe 可空；次数/秒数/sequence 为整数，重量/RPE 为数值。
- 所有数值必须有限；允许零或负的草稿输入，不执行负重、次数、RPE 范围或训练时间业务校验。
  record/set ID 在本训练中必须唯一，workoutId/exerciseRecordId 必须指向所属父记录。
  非法结构、枚举、非有限数值和 ID 不一致返回 422。

成功仅在数据库事务提交后返回 HTTP 200：

```json
{"userId":"c6735859072e4a63abf885cd2be5f319","sourceId":"local-install-001","workoutId":"local-workout-001","version":1,"uploadedAt":"2026-09-07T01:00:02.123456Z"}
```

数据库复用已有 DATABASE_URL、连接池和启动生命周期，新增 `user_workout_snapshots` 表，原 `workout_snapshots` 保留供迁移核对。
复合主键 `(userId, sourceId, workoutId)`；每个训练的 records/sets 作为 JSON 聚合在一个事务中整体替换，
新快照中缺失的旧记录/组随之移除，不会留下孤立子项。更高版本覆盖当前快照；同版本且规范化内容
相同的请求返回原确认和原 uploadedAt；同版本内容不同或较旧版本返回 409。并发写由数据库原子
版本条件和事务保证，MySQL 首次插入冲突/死锁可重试。数据库不可用或提交失败返回 503，
客户端应保留待上传状态，使用原版本及内容重试；不能将 409/422/503 视为上传确认。

删除上传必须为 `{"sourceId":"local-install-001","version":2,"deleted":true,"workout":null}`。
`deleted` 与 workout 是否为 null 必须一致。删除标记持久保留，较旧的迟到上传不能复活已删除训练。
历史回填与实时保存使用同一 PUT 协议；允许从任意正版本开始，也允许首次上传就是删除标记。
服务端不主动创建训练、不反向改写 iOS 本地数据，不把已上传训练自动加入模型上下文。

调试读取同样按当前 userId 和 sourceId 隔离：

- `GET /v1/workouts/{workout_id}?sourceId=local-install-001` 返回确认五字段（含 userId）加 `deleted, workout`，
  未知键返回 404；删除标记仍返回 200。
- `GET /v1/workouts?sourceId=local-install-001&limit=50&offset=0` 按 workoutId 排序分页，
  返回相同结构的数组（包含删除标记），limit 为 1～100，offset 非负。空来源返回空数组。

```bash
curl -X PUT http://127.0.0.1:8000/v1/workouts/local-workout-001 \
  -H 'Content-Type: application/json' --data-binary @tests/fixtures/workout_upload.json
curl 'http://127.0.0.1:8000/v1/workouts/local-workout-001?sourceId=local-install-001'
```


## 历史数据迁移

迁移分为有独立版本标记的两步，各步的数据和标记同事务提交，SQLite/MySQL 均可重试。
单用户旧数据先通过 v1 迁移获得明确归属，旧 workout_snapshots 内容保留。
v2 新增 `user`（id、display_name、created_at、updated_at）、`user_access_tokens`、
`legacy_user_aliases`、`user_installations`；每个旧 ID 映射到唯一随机内部 ID。
旧名称作为 display_name 保留，旧 users 创建时间保留；旧 user_tokens 的 token ID、哈希、
过期和撤销状态完整复制，更新 conversation_owners、image_owners、user_workout_snapshots
的归属，训练版本/删除标记/JSON/上传确认时间及聊天轮次/图片关联保持不变。

旧别名表只用于兼容查找，不作为新数据归属。已有绑定、迁移映射和 token 撤销不因重启重建。
后续更换 DEFAULT_USER_ID 不会新增别名或重新分配历史数据，新孤立文件也不会自动被收养。
旧 users、user_tokens、workout_snapshots 保留供核对，不再与活动表双向同步。
升级应停止旧服务并备份数据库与图片目录；直接回退旧服务不能包含新写入或新 token 撤销。
# Training decisions (v1 prototype)

`POST /v1/chat` additionally supports response kinds `review` and `session_plan`.
Both keep `cards: []`; legacy search remains at most 2 cards and single-exercise
recommendation at most 3. NDJSON keeps its existing events: the final `done.response`
contains `review` or `session_plan`, and `text` renders the structured result.
Chat turn and generated plan commit in one database transaction before `done`. Executable full-session text is withheld until that commit. Dose-free theme/rationale may stream earlier with an explicit provisional label; ordinary chat streams immediately.

- `GET /v1/training/profile`: latest `{version, profile, saved_at}`; version 0 has unknown goals/state.
- `PUT /v1/training/profile`: `{expected_version, profile}`. Explicit full replacement,
  append-only version history. Same content at the next version replays; stale updates return 409.
- `GET /v1/training/options`: active catalog equipment codes with Chinese labels.
- `GET /v1/training/review`: computed facts, independent of model access. Query fields:
  `end_date` (exclusive local date), `recent_days` (1–90, default 7),
  `baseline_days` (1–180, default 21), `time_zone` (IANA, default UTC).
  Missing end date includes today up to generation time. Windows use workout start time.
  `selection_mode` is `recent_workouts` (default) or `date_window`; `workout_count` is 1–100,
  default 7. Generic history review selects the latest 7 completed workouts across all history,
  not the last 7 days. Fewer available workouts are all used and explicitly reported.
  `end_date` remains an exclusive cutoff when combined with explicit `recent_workouts`.
  Legacy calls explicitly supplying `recent_days`, `baseline_days` or `end_date` without a
  selection mode retain date-window behavior. Empty options/timezone-only queries use counts.
  `scope` is `multiple` (default) or `single`. `single` without IDs selects the latest completed,
  non-deleted current-user snapshot before the cutoff, excluding future starts; it is not limited
  to the recent/baseline windows. To select a workout, supply **both** `source_id` and `workout_id`.
  IDs require `scope=single` (otherwise 422). Missing, foreign, deleted or active selections return
  404; the server never substitutes a different workout. Empty latest-single selection also returns 404.
- `GET /v1/training/plans`: bounded decision context (up to 5), retaining latest accepted
  and latest executed plan before filling recent proposals.
- `GET /v1/training/plans/{id}`: immutable plan, lifecycle status/revision, latest 10 events,
  truncation flag and separately retained execution evidence.
- `GET /v1/training/completed-workouts`: latest 50 current-user completed snapshots for linking.
- `POST /v1/training/plans/{id}/events`: `{event_id, expected_revision, kind, reason?, workout?, effort?}`.
  Kinds: `accepted`, `skipped`, `executed`, `feedback`. Only an accepted plan can become executed.
  Skipping requires a reason; execution requires `{source_id, workout_id}` owned by this user.
  Effort is `{scale: "RPE"|"RIR", value: 0..10, timing: "last_working_set"|"session",
  observed_at: timezone-aware timestamp, familiar_with_scale: boolean}`.
  Repeated identical event IDs replay their saved receipt; stale revisions conflict.
  Actual execution saves snapshot version and recorded/expected sets; it never edits iOS snapshots.

Profile fields: `goal` (`strength`, `hypertrophy`, `general_fitness`, or null), `goal_notes`,
`priority_exercise_ids`, `experience`, `weekly_sessions`, `session_minutes`, `equipment`,
`time_zone`, optional dated `readiness` (`pain`, `fatigue`, `sleep`, `notes`) and optional
`confirmed_cycle` (`phase`, `starts_on`, `ends_on`, `reason`). Goal/profile writes are explicit
UI/API actions. Extracted conversational overrides affect the current plan and are captured
in that plan's profile snapshot; they do not silently overwrite saved preferences.

`context.review_options` selects review scope, workout and windows. Explicit client option fields take precedence over classifier defaults. `context.plan_to_modify` is a one-use explicit
parent reference for a replacement plan. The server replaces echoed `training_review`,
`training_profile`, `session_plans`, and `workout_history`; callers cannot supply trusted evidence.
Model-selected parent IDs are checked for current-user ownership and the original is loaded
before replacement generation. Successors preserve their original and increment the chain version.

Review totals read all matching completed snapshots, not the last-30 chat projection. Drafts,
unfinished sets and tombstones are excluded. Completed-set counts include invalid raw numeric
entries, flagged separately; invalid doses are omitted from performance observations. Main body
part attribution counts each set once. Observations are bounded to 200 and source/set references
are bounded independently; truncation is explicit and never changes totals. Multiple source IDs
remain distinct backups; the server does not infer that two device snapshots are duplicates.

The initial rule subset is versioned and distinguishes research from product choices. Missing
goals/conditions/readiness prompt for clarification. Plans use active catalog IDs and available
equipment, protect priority order, calculate preparation/execution/rest/switching/buffer time and
remove lower-priority items when necessary. Alternatives require replanning and do not transfer
load. Historic load convention and effort are unknown until a user explicitly confirms the corresponding observation. Numeric history load is a trial starting point; progression additionally requires the evidence checks below. Exact fatigue matrices,
effective-set formulas, personalized recovery scores and autonomous rehabilitation are not provided.

## Offline browser verification

`uv run python scripts/training_decision_test_server.py` starts a synthetic-only playground at
`http://127.0.0.1:18082/playground/` with a temporary SQLite database and deterministic model.
It never reads the development MySQL or invokes an external model. It is a UI/contract fixture,
not a model-quality evaluation. Python suites and `node --test tests/web/*.test.mjs` cover core contracts.

## Observation confirmation and progression eligibility

`PUT /v1/training/observations/{observation_id}/attestation` accepts:

```json
{
  "expected_version": 0,
  "source": {"source_id": "installation", "workout_id": "workout", "version": 1, "record_id": "record"},
  "confirmed": true,
  "load_convention": "per_hand",
  "conditions": "Same bench, dumbbells, range of motion and technique",
  "effort": {"scale": "RIR", "value": 3, "timing": "last_working_set", "observed_at": "2026-09-07T01:00:00Z", "familiar_with_scale": true}
}
```

Use the observation ID and exact source object returned by `/review`. Confirmation versions are
append-only and user-scoped. Repeating the same next-version payload replays; stale confirmation
or workout versions return 409; missing/foreign sources return 404. `confirmed: false` revokes.
Load conventions: `total`, `per_hand`, `added`, `assistance`, `bodyweight`, `machine_stack`,
`machine_level`; allowed values must match the recorded load type, and per-hand values require
handheld weights. `effort` is optional. Its observation time must be on/after the exercise record,
within six hours and not future-dated; this is a capture sanity bound, not a physiological threshold.
The browser preserves the precise original timestamp if the displayed default is unchanged.

`review.observations[].attestation` includes the latest confirmation/version. Replacing a workout
changes observation IDs, so earlier confirmations cannot carry over. `comparison_eligible` indicates
confirmed recording conditions, not demonstrated improvement. Generic plan/session effort feedback
does not establish exercise-specific effort. No request can directly set trusted eligibility.

`progression_signals` describes conservative product eligibility, not a research-derived prescription:
only the latest two available performances for that exercise are considered, in distinct completed
workouts with ordered dates; the latest must be within 14 days. They require the same record type,
load type/unit/value/convention, exact user-confirmed conditions, completed-set count, uniform reps
within each performance and no invalid/unfinished sets. Both need familiar last-working-set RIR;
latest RIR must be at least the prior value and both in 2–6; reps must not decline. A newer unusable
record blocks an older pair. Time-based exercises, missing evidence and truncated set references
cannot unlock progression. Synchronization completeness still cannot be inferred.

An increase identifies `progression_exercise_id`, cites both source observations, retains the latest
history starting load and protects that priority item. `increase_reps` adds one rep per set;
`increase_sets` adds one set; `increase_load` preserves sets/reps and asks the user to trial the
smallest available increment only after the starting load meets control/effort conditions. These
single-step rules are product choices. High fatigue, poor sleep, or an active confirmed deload/recovery
phase blocks increases. History evidence/confirmation versions are locked and rechecked before
plan commit; changes during generation reject the result before executable plan text or `done` is emitted; any earlier qualitative text remains labeled provisional.
Other interpretations remain model-generated and should be evaluated separately.

Review numbers and per-record dose/effort are rendered from server facts. Model prose does not
recalculate numeric counts or relabel completed sets as effective sets. Review schemas enumerate only current evidence IDs. Individual findings that fail evidence/prose validation are omitted without assigning substitute references; valid findings and server facts survive. `review.omitted_findings` records omissions and the rendered report explains them. Optional alternative exercises that fail catalog/equipment/
target/dose checks are omitted and recorded in `discarded_alternatives`; primary work is still
validated in full. Titles may repeat only the user's exact available time; computed duration remains
the feasibility check.


## Conversational planning before generation

Intent routing distinguishes asking how recommendations work (`chat`) from requesting an actual
session (`session_plan`); a correction of conversational style follows the latest user intent.
Missing profile fields do not replace an assistant turn with a fixed questionnaire. A session request
with incomplete prerequisites streams a history-aware discussion through the ordinary response path,
with `session_plan: null` and no persisted plan. Necessary-input and evidence checks still apply to
actual structured plan generation. No goals or readiness are silently inferred into saved preferences.
The assistant should answer what can be answered from existing records before deciding whether a
question is necessary, and respect a request to analyze first instead of repeating an unanswered prompt.

### Structured suggestion cards (2026-09-12)

`ChatResponse.suggestion_kind` is `single_exercise`, `alternatives`, `session_plan`, or null. Alternatives mean choose one; a session means perform its items in order. Search cards remain informational. Recommendation cards now contain `set_prescriptions`; session `items` use the same array.

Incomplete session requests can return `kind=session_plan`, `session_plan=null`, `suggestion_kind=null`, and `plan_preview={status,theme,next_question,target_date}`. Status is `needs_input` or `blocked`. The `cards` array contains ordered, informational catalog candidates: `set_prescriptions=[]` and `can_add_to_active_workout=false`. This preview creates neither a stored SessionPlan nor a workout. Candidates are restricted to known equipment or resolvable historical catalog exercises; without sufficient catalog context it may be empty. Reported pain blocks candidate generation. A fully specified request still follows the existing validated SessionPlan flow.

Preview model output enumerates allowed catalog IDs. Unknown, unavailable or duplicate selections are excluded while valid candidates retain their order. If any selection is excluded, the overall explanation is replaced to describe only the remaining candidates. Dose-bearing preview prose is replaced with a qualitative pending-conditions message before schema validation; unexpected prescription fields remain invalid. These recoveries do not invent replacement exercises or create executable prescriptions.

Each prescription is one planned group:

```json
{"kind":"working","repetitions":8,"duration_seconds":null,"rest_seconds":120,
 "target_rir_min":2,"target_rir_max":3,
 "load":{"strategy":"trial","value":null,"unit":"kg","convention":null,"explanation":"重量待试做确认。"}}
```

`kind` is warmup/working. Repetitions and duration are mutually exclusive and must agree with the catalog detailType. Unknown weight remains null. Numeric user-entered load requires kg/lb or an integer machine level, with an explicit convention: total/per_hand/added/assistance/machine_stack/machine_level. One exercise cannot mix numeric units or conventions. Bodyweight/duration rows have no external load. Every exercise must retain a working set, at most 12 total rows. User edits allow repetitions 1–1000, duration 1–3600 seconds, rest 0–900 seconds, target RIR 2–6. Model candidate limits are narrower.

Explicit rows are authoritative. Legacy `sets` means working-set count; `total_sets` includes warmups. Aggregate repetitions/duration/rest are conservative summaries and do not mean every row is identical. Clients expand legacy item fields only when `set_prescriptions` is absent. Time includes execution of every row, each non-final row's rest, transitions and plan overhead.

`POST /v1/training/plans/{id}/revision` accepts:

```json
{"revision_id":"7838540d-3a1b-4cc5-874d-6714f1ef73fb","expected_revision":0,
 "items":[{"exercise_id":"0289","set_prescriptions":[
   {"kind":"working","repetitions":8,"duration_seconds":null,"rest_seconds":120,
    "target_rir_min":2,"target_rir_max":3,
    "load":{"strategy":"user","value":15,"unit":"kg","convention":"per_hand"}}
 ]}]}
```

The response is a proposed plan snapshot with a new immutable `plan.id` equal to revision_id and incremented `plan.version`. Reordering, removing exercises, and editing their sets are supported; replacing an exercise requires re-planning. User edits are marked `user_edited`, numeric loads become user choices, and old progression claims are cleared. Recompute the time budget; an oversized edit returns 422, without altering the original. The original plan payload is unchanged, its lifecycle revision advances, and `superseded_by` points to its successor. Repeated identical revision_id returns its original receipt; conflicting IDs, stale revisions, or superseded parents return 409. Regenerated model successors use the same parent revision concurrency protection.

Confirm the **new** plan via the existing accepted event. Web confirmation persists a chosen plan, never a workout. Flutter receives accepted plans for the same account; explicit Start creates ordered incomplete native records. Single-action additions validate the real local active/paused workout. Local pending/started state and duplicate protection are owned by Flutter. An accepted empty rest recommendation is not an actionable workout.

`GET /v1/exercises/{id}/thumbnail` serves the verified catalog's local JPG, or 404 if absent. `EXERCISE_MEDIA_DIRECTORY` configures the source folder (default: adjacent flutter-init assets/exercises/images). Authentication matches other API reads; the playground fetches thumbnails with its active identity. No image is generated or inferred by the model.


## Expert skills and review scopes (2026-09-17)

The five intents and existing response forms are unchanged. Packaged, versioned
`training-review` and `training-recommendation` method documents are selected by the server.
Review uses the review skill; recommendation, informational preview and complete/revised plan
use the recommendation skill. Recommendations consume computed review facts directly and do
not require a preliminary model review call. Clients cannot specify skill names, paths or content.

Examples (same fields are accepted in `context.review_options`):

```text
GET /v1/training/review?scope=single
GET /v1/training/review?scope=single&source_id=local-install-001&workout_id=workout-123
GET /v1/training/review?scope=multiple&end_date=2026-09-17&recent_days=7&baseline_days=21
```

Facts use `rules_version=training-review-v3`, retaining existing aggregate/observation fields:

- `scope`: `single` or `multiple`.
- `selection`: resolved mode (`single`, `recent_workouts`, `date_window`), requested/selected
  workout counts, insufficient-history flag and whether a baseline was actually included.
  Count mode aggregates **only** the selected workouts into `recent`; date fields describe
  actual sample span. Weekly rates are null, baseline is excluded from evidence IDs, and
  a zero baseline is not evidence of no prior training. No date-based plan execution ratio is
  computed for count-selected samples. Full limitations remain structured and expandable in Playground.
- `session`: single only. Exact source/version, recorded start/end and duration, pause-adjusted
  duration when valid, ordered non-draft records and sets, type-appropriate numeric set changes,
  catalog muscle overlaps, and recorded entry gaps. Actual `rest_seconds` is always null:
  snapshots contain no per-set start/end timestamps. Values are records, not claims of fatigue,
  progress or recovery. Unknown duration stays null. Duplicate sequence values flag order ambiguity;
  no changes or adjacent-muscle inferences are derived across ambiguous positions.
- `cadence`: up to the latest 200 sessions across the selected windows, sorted chronologically,
  adjacent start gaps in seconds/hours, per-primary-body-part recurrence intervals, full
  `total_workouts` and `truncated`. Rounding hours never removes the exact seconds evidence.
- `cycle`: `confirmed` or `unknown`, the saved user-confirmed cycle and a separate date-overlap flag.
  Observed cadence cannot establish a cycle; a saved cycle outside the reviewed dates is not current.

Single scope keeps `recent` as the selected day's **selected-workout-only** aggregate; compatibility
`baseline` is empty and is explicitly not analyzed. The report does not render a baseline comparison
or extrapolate single-session frequency. Date-window mode preserves full-window counts independently
of bounded detail; default count mode deliberately limits the selected set to `workout_count`. Session details cap records and sets at 100 each and expose truncation flags;
invalid completed sets remain counted but do not enter numeric change evidence.

Review findings can reference `session.sequence`, `session.timing`, `cadence.spacing`,
`cycle.context`, existing `recent/baseline.frequency/distribution`, or observation IDs. The server
resolves those IDs to facts. Reports add `charts` to `{facts, findings, keep, next_adjustments, question}`;
model claims remain marked inference. Stored reports and authoritative server context survive replay.
The skills do not update actual workouts; iOS remains the owner of training execution.


## Structured response streaming (2026-09-17)

The NDJSON event types are unchanged. Review starts with a `text_delta` containing the
server-computed selection summary, then emits complete validated findings as the model's tool
arguments stream. Partial JSON objects/strings, unknown references and rejected conclusions are
never published. Findings appear once; `done.response.text` matches the accumulated review text.
If no model finding validates, the result still contains server facts and an explicit omission note.
Malformed/incomplete responses or provider failures retain the text already shown and terminate
with `error`; they are not reported as successful complete reports.

Complete plans and informational previews stream closed, dose-free theme/rationale sections
under “方案构思 · 生成中，尚未确认”. Those sections are provisional, not executable prescriptions.
Final catalog, dose, ordering, time and current-evidence checks remain required. A complete plan
is committed before executable text and `done`; failed commits never publish an executable plan.
Clients must replace provisional text with `done.response.text`, as the Playground already does.
Replay continues to return the saved authoritative `done` without re-running generation.
Disconnect cancels provider streams and persists visible partial text with cancellation status.


## Narrative reviews and charts (2026-09-17)

`review.findings[].title` is optional. `conclusion` supports natural Markdown paragraphs,
lists and emphasis; the model schema no longer requires repeated comparison/explanation/action/
missing-information fields. Legacy fields remain accepted for compatibility. Empty `keep` and
`next_adjustments` arrays avoid repeating directions already explained in the narrative.

Only the model-facing prompt uses allowlisted `{{fact:KEY}}` references. The server resolves them
against the selected review snapshot and verifies that the finding cites the slot's evidence ID.
Unknown or uncited slots invalidate that finding; placeholders cannot appear in titles or tail text.
Both streamed text and persisted findings contain resolved values, never internal placeholders.
Freeform model-generated numbers remain rejected. This permits precise facts in readable prose
without allowing the model to fabricate or recompute training measurements.

`review.charts` contains deterministic chart objects:

- `id`, `kind` (`timeline`, `columns`, `bars`), `title`, `description`, `unit`, `truncated`.
- `points`: `{label, value, at?, source?}`. Session points include exact ISO start time and source/
  workout/version references. Display-date labels respect the review timezone.
- Timeline positions follow actual elapsed time. Session columns start at zero and count all
  recorded completed sets. Body-part bars partition completed sets by catalog primary body part;
  unrecognized exercises remain visible as an unknown category. No mixed-unit tonnage is inferred.
- In date-window mode charts include the comparison period and label it. Detail is bounded by the
  existing cadence cap; charts report truncation rather than implying full-history coverage.
  Charts with no data are omitted; a single session does not generate a cadence timeline.

`facts.cadence.sessions[].completed_sets` and `facts.body_part_labels` supply these views.
Playground renders charts after the narrative and collapses deduplicated evidence and limitations.
Charts and their exact-data tables survive history replay. Existing clients can ignore `charts`.


## Ordered report composition v1

Production review calls now run `understand → retrieve → compose → respond`. The composition call
uses the review expert method and server facts to choose text purposes/evidence and available chart
keys; it does not generate chart measurements. The compiler allocates UUID-based block, artifact and
dataset IDs, merges adjacent text intents, and freezes the outline before the final generation call.
`result_id` identifies the final report; per-request event envelopes provide identity during streaming.
This adds one model call to review generation. Other intents and older custom adapters retain their
existing response path; their `schema_version` is null and clients use legacy rendering.

The authoritative final response adds these top-level fields:

| Field | Contract |
| --- | --- |
| `schema_version` | `1` for ordered reports; null/absent means legacy |
| `outline` | Ordered block descriptors with unique IDs. Markdown entries contain `purposes`, `evidence_ids` and allowed `fact_ids`; chart entries contain `artifact_id` |
| `blocks` | Ordered `{id,type:"markdown",content,parts}` or `{id,type:"chart_ref",artifact_id}` |
| `artifacts` | Map from artifact ID to portable chart specification |
| `datasets` | Map from dataset ID to `{rows:[...]}` |
| `facts` | Map from fact ID to `{value,evidence_id,source_key}`; `value` is the server-formatted literal display phrase |
| `text` | Compatibility Markdown: nonempty text blocks joined by two newlines; no image/chart markers |

Synthetic contract fixtures: [short IDs and inline parts](../tests/fixtures/report_short_ids_v1.json);
[legacy long IDs](../tests/fixtures/report_composition_v1.json).
A `chart-spec/v1` artifact contains `chart_type` (`timeline`, `columns`, `bars`), `title`, `description`,
`unit`, `dataset_id`, `encoding:{label:"label",value:"value",at:"at"}`, and `truncated`.
Rows contain `label`, finite nonnegative `value`, optional exact ISO timestamp `at` and source/version
reference `source`. Timeline positions are proportional to `at`; columns/bars use a zero baseline.
Display labels use the review timezone. Units and truncation notes are part of the visible contract.
This is a restricted cross-platform chart description, not HTML, SVG markup, ECharts configuration,
or executable code. New chart types require explicit renderer support and a compatible contract.
Current data is bounded inline JSON; no physical data file/download endpoint is created or required.

Additional NDJSON events (the existing request/conversation/sequence envelope still applies):

| Event | Payload and client action |
| --- | --- |
| `report_layout` | `schema_version, outline, artifacts, datasets, facts`: freeze order and register resources |
| `block_start` | `block:{id,type:"markdown",content:"",parts:[]}`: allocate one text block |
| `block_delta` | `block_id, delta, parts`: append resolved compatibility Markdown and inline parts to that block |
| `block_end` | `block_id`: text block finished |
| `block` | `block:{id,type:"chart_ref",artifact_id}`: display the registered chart at this position |
| `done` | Persisted full response; authoritative for final rendering/history, as before |

A text block is published after its structured content closes and passes validation; this release
streams validated sections, not every unvalidated provider token. Charts can appear before later
text blocks finish. Compatibility `text_delta` events are also emitted: a v1 client must not render
them as additional content. Do not reconstruct chart positions by parsing `text`.

Generation uses one structured tool stream for all text blocks, with exact IDs/order. The final tool
arguments must be strictly valid JSON and have a successful provider completion signal. EOF-repaired
JSON, token-limit termination, missing/reordered/rewritten blocks and extra tool calls cannot produce
successful `done`. Invalid prose gets at most one targeted model repair before publication, with
its frozen block ID, evidence IDs and allowed fact slots. Valid repairs increment
`review.repaired_findings`. A still-invalid block is replaced at its planned position and increments
`review.omitted_findings`; unverified values never reach the client. If every analysis block fails,
the turn emits an error (never successful `done`) while preserving emitted facts and charts.
The web labels partially omitted reviews “部分完成”, including historical responses; old all-omitted
responses show “分析未完成” and expose retry.

Invalid composition gets one retry with a stable validation reason. If still invalid, a server-built
outline retains available chart topics and their relevant evidence instead of losing all charts.
Trace includes `layout_fallback` and `fallback_reason`; diagnostic logs record only request IDs,
attempt counts and fixed reason codes, never raw user/provider content.
Markdown image syntax and raw HTML are rejected, so chart resources cannot be smuggled through text.
New resources use a type prefix and exactly eight lowercase hexadecimal characters:
`block_d903ab41`, `chart_b7e104d2`, `data_c812fa30`, `fact_a3f29c01`.
The server allocates these once per compiled report and checks for collisions (retrying allocation).
Uniqueness is report-local; address resources across reports using `(result_id, resource_id)`.
A persisted replay keeps its IDs; a newly generated report has a new set. Existing history with
long IDs or without inline parts remains valid; clients must not reject it based on ID length.

The model writes `{{fact:fact_a3f29c01}}` using only the current block's allowed `fact_ids`.
The server resolves each exact ID against `facts`, verifies its cited evidence, and splits the
validated content into `parts:[{type:"text",text:"记录包含 "},{type:"fact_ref",fact_id:"fact_a3f29c01"}]`.
Unknown IDs, a chart ID used as a fact, and uncited references fail validation. `source_key`
is descriptive metadata, never an identifier clients or the model should parse or regenerate.
Catalog labels with spaces therefore do not affect reference syntax.
`content` and top-level `text` retain resolved Markdown for compatibility. New clients render
`parts` in sequence, resolving `fact_ref` from the same response's fact registry and treating
fact values as literal text, not HTML/Markdown. Never render both parts and content.
If `parts` is absent, fall back to `content`; missing facts get a readable placeholder.
This is an additive v1 extension: legacy clients can continue using resolved `content`/`delta`.
Exact numeric facts are resolved before publication; raw Arabic and Chinese numeric quantities
remain subject to the same validation.
A reference such as “这 7 次训练” may be bound automatically only when its count exactly matches
the selected recent-workout sample and the block cites frequency or complete cadence evidence.
This does not approve frequency claims such as “每周 7 次” or other doses/loads.
Slot values include their units and describe the specified scope.

The repair model receives only the current draft, concrete failing sentences, cited evidence and
allowed slots; it does not receive conversational history or other uncited records. It returns
only `content`, and the server retains the original block ID. Explicit negative statements such
as “不能据此认定恢复训练” are not treated as affirmative claims; separate positive claims remain checked.

Failed/cancelled turns retain emitted blocks, resources and consistent compatibility text in their
stored `response`, marked `incomplete:true`; the row remains failed/cancelled. An empty started block
may remain. Do not show planned but unemitted chart/text blocks as completed. Failed turns do not emit
successful `done` or enter model history as completed replies. Successful retries with the same
request/conversation reuse the persisted IDs and datasets instead of regenerating the report.

Flutter adoption:

1. Parse v1 fields alongside existing text/cards/plan fields; for null/unknown versions retain legacy
   text rendering. The current Flutter client has not yet implemented these new report renderers.
2. Process ordered block events, or `blocks` from the final/history response. Resolve
   `chart_ref → artifacts[artifact_id] → datasets[dataset_id]` and inline
   `fact_ref → facts[fact_id].value` using the response snapshot only.
3. Render the supported Markdown subset with HTML and remote images disabled. Draw the three supported
   chart types natively or in a WebView without changing backend payloads; preserve axis/date/unit rules.
4. Missing/unsupported chart formats show a readable fallback and an exact-data table when available.
   Never execute strings in chart specs or fetch user/provider-supplied asset URLs.
5. Replace provisional state with authoritative `done`, preserving keyed chart UI where unchanged.
   Verify interleaving, duplicate suppression, cancel/error history and replay against the fixture.

Playground now renders these blocks directly, suppressing the old end-of-report chart list for v1.
Static assets use `Cache-Control: no-cache`; the versioned entry URL
`/playground/?v=report-blocks-v1` also bypasses previously cached HTML on first migration.

## Personal exercises and authoring drafts

`kind=custom_exercise` invokes the packaged `exercise-authoring` skill. **Chat only produces
metadata; it never inserts a personal exercise, even when the chat text asks to save directly.**
The user must confirm in the frontend, which calls a separate save endpoint.

A complete authoring response has empty `cards` and:

```json
{
  "custom_exercise": {
    "status": "draft",
    "draft_id": "<chat request_id UUID>",
    "version": 1,
    "definition": {
      "name": "墙天使", "aliases": ["Wall Angel"],
      "detail_type": "bodyweightReps", "body_part_code": "back",
      "equipment_code": "body weight", "target_muscle_code": "unknown",
      "secondary_muscle_codes": [], "training_type": "mobility",
      "instructions": [], "default_weight_unit": null,
      "inferred_fields": ["body_part_code", "training_type"]
    },
    "avatar": {
      "kind": "builtin", "key": "mobility",
      "url": "/v1/custom-exercises/avatars/mobility"
    },
    "labels": {
      "body_part": "背部", "equipment": "徒手", "target_muscle": "待补充",
      "secondary_muscles": []
    }
  }
}
```

`context.custom_exercise_drafts` supplies up to three unsaved drafts from server-owned conversation
history for follow-up edits; client-supplied values are replaced and excluded from request identity.
The definition above illustrates the wire shape, not an authoritative anatomical classification.
`inferred_fields` records model-completed fields, not user-confirmed expert facts. Unknown
classification uses `unknown`; body part, equipment and muscle codes are validated against catalog
terms. `training_type` is separate: strength/cardio/mobility/stretching/other. `detail_type` uses the
same four recording types as existing exercises. Name and recording type are required; relevant
ambiguity returns `status=needs_input` with `question`, with no catalog mutation. An existing match
returns `status=existing` and an actual visible exercise card. Clients must not treat drafts as
selectable exercise records or count them in workouts.

All endpoints use the existing authenticated/current-user identity. No caller-supplied owner is accepted.

| Method | Route | Purpose |
|---|---|---|
| GET | `/v1/custom-exercises/options` | Allowed terminology and training types |
| GET | `/v1/custom-exercises/avatars/{key}` | Authenticated built-in SVG draft avatar |
| PUT | `/v1/custom-exercises/drafts/{draft_id}` | `{version, exercise: definition}`; updates chat draft only, returns refreshed metadata and incremented version |
| POST | `/v1/custom-exercises/drafts/{draft_id}/save` | Explicit frontend confirmation with `{version}`; saves that server-held draft version |
| POST | `/v1/custom-exercises` | Explicit frontend save of `{exercise: definition}` from a manual form |
| GET | `/v1/custom-exercises?include_archived=false` | Current user's saved definitions and versions |
| GET | `/v1/custom-exercises/{id}` | Current user's definition, exercise and version |
| PUT | `/v1/custom-exercises/{id}` | `{version, exercise: definition}`; full metadata replacement, also restores archived entries |
| DELETE | `/v1/custom-exercises/{id}?version=N` | Archive; does not delete historical records |

Draft editing is available directly on the web card. Name, aliases, classification, muscles,
recording type, weight unit and instructions can be edited before confirmation. Applying edits
updates only chat metadata; it does not insert a catalog record. Classification labels and the
builtin avatar are refreshed by the server. Changed fields are removed from `inferred_fields`;
unchanged expert inferences retain their markers. Refreshing restores the edited draft. Original
assistant text remains an immutable statement from that turn.

Legacy drafts without a version are version 1. Omitting the confirmation body is supported only
for version 1. Edits and confirmation reject stale versions with 409, including stale retries after
another client saved a newer version. Already-saved drafts cannot be edited; use the saved record
endpoint. Owners are checked on every draft operation.

Authoring resolves the exercise name before metadata completion. A high-confidence typo or
homophone correction proceeds directly to a draft, with a brief notice of the adopted name and
`custom_exercise.name_correction: {input_name, resolved_name}`. Ambiguous candidates or conflicting
descriptions still trigger a clarification. Explicit user naming and rejections take precedence.
Erroneous names from the current input or prior draft are excluded from aliases, including their
“动作” suffix variants; explicitly retained aliases remain valid. This changes draft generation
only: saving still requires the independent confirmation action. Unsaved draft IDs cannot stand
in for catalog IDs. Non-matching catalog names receive a separate movement-identity check;
similar exercises cannot substitute for the requested movement. Invalid existing-record references or inconsistent names
trigger one bounded model repair; unresolved cases yield a clarification instead of failing chat.

Confirmation revalidates the draft, checks standard and personal names/aliases, then saves a record
and `custom_exercise.saved_result` on the source chat response in **one transaction**. Chat text is
unchanged. Retrying confirmation returns the stored receipt; confirming different drafts for the
same name reuses an existing entry. Another account cannot read or confirm the draft. Failed saves
roll back both changes and can be retried. Refreshing a conversation restores the pending/saved state.
The direct save endpoint also deduplicates names, but is not callable by the chat graph.

Receipts contain `status=created|existing`, `exercise`, and (for custom records) `definition` and
`version`. New IDs are server-generated `cx_` plus eight hexadecimal characters. Definitions live
in the owner-scoped `custom_exercises` table; the standard snapshot and checksums are unchanged.
Exercise objects preserve the existing camelCase contract and add `sourceKind=custom`,
`trainingType`, and `avatar={kind,key,url}`. Avatar keys map to five built-in category icons, not
photographs of a verified exercise technique. Saved avatar URLs use the existing owner-checked
`/v1/exercises/{id}/thumbnail` endpoint. Web clients can display the authenticated SVG response;
Flutter clients can render the same asset with SVG support, or map the stable icon key to a native
asset. This backend change does not implement the Flutter UI.

`GET /v1/exercises` searches standard plus current-user active personal entries. Exact lookup and
avatar access retain archived personal records for history; selection excludes them. Chat search,
recommendation and plan validation use an isolated per-user catalog view. Review reads saved
custom classifications, including archived records. Imported history IDs are **not** automatically
rewritten or linked. Changing recording type requires a new exercise to preserve old set semantics.
Updates use optimistic versions (409 on stale state), invalid metadata returns 422, and foreign
or missing private records return 404.
