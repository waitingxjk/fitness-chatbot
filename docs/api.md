# HTTP API v1

## 路由

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/playground/` | Web 流式聊天测试界面 |
| GET | `/health` | 服务存活、目录数量/版本、聊天配置状态（不探测供应商） |
| GET | `/v1/exercises?q=卧推&equipment=dumbbell&limit=20` | 本地目录检索 |
| GET | `/v1/exercises/{exercise_id}` | 完整动作详情；未知 ID 返回 404 |
| POST | `/v1/images` | 上传一张 JPEG / PNG / WebP 原始文件体，返回图片 ID |
| GET | `/v1/images/{image_id}` | 查看 chatbot 本地图片 |
| POST | `/v1/chat` | 流式聊天 |
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
- 训练快照仅代表客户端提供的事实，没有账户查询或自动写训练操作。

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
不改变原有表结构。相同内容上传返回相同 ID；上传成功但尚未发送的文件也保留。

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
`kind` 是 search / recommendation / chat。

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
此版本是本机开发接口，尚未实现账户鉴权，不能据 conversation_id 推断访问授权。

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
