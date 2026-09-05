# HTTP API v1

## 路由

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 服务存活、目录数量/版本、聊天配置状态（不探测供应商） |
| GET | `/v1/exercises?q=卧推&equipment=dumbbell&limit=20` | 本地目录检索 |
| GET | `/v1/exercises/{exercise_id}` | 完整动作详情；未知 ID 返回 404 |
| POST | `/v1/chat` | 流式聊天 |

目录过滤参数还有 `body_part`、`target_muscle`，可用英文 code 或术语表中文名。
多个条件取交集；`q` 对中文名称、英文名称、别名作子串匹配。limit 为 1～100。
未提供检索条件时返回空列表。此接口不消耗模型调用。

## 聊天请求

`Content-Type: application/json`。最简请求只需要 `message`。

```json
{
  "message": "第二个换成无器械",
  "conversation_id": "d0dc53a0-f613-47bc-a2a1-96b3c2e38f50",
  "history": [
    {"role": "user", "text": "帮我找核心动作"},
    {"role": "assistant", "text": "找到了两个核心动作。"}
  ],
  "previous_result_ids": ["0001", "0002"],
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

- `message` 去除首尾空白后 1～4,000 字符；历史最多 30 条，每条最多 4,000 字符。
- 历史角色只接受 `user` / `assistant`。训练快照最多 100 条动作记录。
- `conversation_id` 为 UUID，省略时创建；后续请求需要同时传 ID 和历史，ID 不代表服务端记忆。
- `previous_result_ids` 最多 3 个，保留上一组卡片顺序，以理解「第二个」「换一个」。
- `active_workout` 可为 null；状态接受 active、paused、completed、discarded。
- 训练快照仅代表客户端提供的事实，没有账户查询或自动写训练操作。

## NDJSON 响应

HTTP 200，`Content-Type: application/x-ndjson`，UTF-8，每行一个 JSON 对象，以 `\n` 结束。
文本中的换行由 JSON 转义。每个事件包含 `request_id`、`conversation_id` 和从 1 递增的 `sequence`。

| type | 其他字段 | iOS 行为 |
| --- | --- | --- |
| `status` | `stage`: understanding / retrieving | 显示处理中 |
| `text_delta` | `delta`: string | 追加到当前助手文本 |
| `exercise_cards` | `kind`、`result_id`、`cards` | 展示横向动作卡组 |
| `done` | `response`: 完整回复 | 标记成功，用完整回复校准当前轮次 |
| `error` | `code`、`message` | 标记失败，保留输入并提供重试 |

`exercise_cards` 在文本生成成功后发送，无卡片时省略。正常流最后为 `done`；
失败流最后为 `error`，不会随后发送 `done`。网络断开可能没有终止事件，必须视为未完成。
`done.response` 包含 `conversation_id`、`result_id`、`kind`、`text`、`cards`、`catalog_version`。
`kind` 是 search / recommendation / chat。

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
生成过程失败因 HTTP 响应已开始，以流内 error 表达：`timeout` 或 `generation_failed`。
默认整轮超时 90 秒，可通过 `CHAT_TIMEOUT_SECONDS` 调整。
代理需关闭响应缓冲；客户端取消请求会取消相应生成任务。

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
