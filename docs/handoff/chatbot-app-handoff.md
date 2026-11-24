# Chatbot → App 接入交接

更新：2026-09-18。实现基线：`acd9cb5`，分支 `codex/training-decision-v1`。
本文描述**当前已经实现的服务端协议**，面向 Flutter / iOS App；不要求复用 Playground 的 HTML。

配套文件：

- [完整请求与返回样例](examples/chatbot-responses.json)：8 个场景，包含草稿编辑/保存、计划状态、复盘重放。
- [复盘完整 NDJSON 流](examples/review-stream.ndjson)：可作为客户端流式解析与渲染测试输入。
- [完整 API 文档](../api.md)、[服务端 OpenAPI](http://127.0.0.1:8000/docs)。真机需替换为开发机地址。

样例使用模型替身、标准动作目录和隔离 SQLite 生成，不包含真实用户数据，不代表模型质量评估。
样例里的 ID 只用于渲染测试；联调保存操作必须使用目标服务器实际返回的 ID 和版本。

## 1. 当前能力和 App 呈现

| 用户意图 | `response.kind` | 输出及交互 |
| --- | --- | --- |
| 咨询、追问、图片理解 | `chat` | Markdown 正文，可结合服务端对话与训练上下文 |
| 查找动作 | `search` | 正文 + 最多 2 张真实目录动作卡；无匹配时解释 |
| 推荐动作或替代动作 | `recommendation` | 最多 3 张卡，可含逐组建议；多个备选通常任选一个 |
| 复盘一次/多次训练 | `review` | 有序正文块 + 图表引用 + 图表/数据/事实资源；可展开依据与范围说明 |
| 生成或修改整次计划 | `session_plan` | 条件不足返回待完善预览；条件充分返回有顺序、逐组处方的完整方案 |
| 创建自定义动作 | `custom_exercise` | 追问、命中已有动作，或可编辑的动作草稿；用户确认后独立保存 |

专家方法由三个服务端 skill 提供：[训练复盘](../../src/fitness_bot/skills/training-review/SKILL.md)、
[训练推荐](../../src/fitness_bot/skills/training-recommendation/SKILL.md)、
[动作建档](../../src/fitness_bot/skills/exercise-authoring/SKILL.md)。
推荐可使用已计算的复盘事实，不必先生成一份复盘文章。App 不需要传 skill 名称或拼装 prompt。

几个业务边界必须保留：

- 复盘默认取**最近 7 次已完成训练**，不是最近 7 天；不足 7 次就分析实际已有记录。
- 推荐的 `alternatives` 是备选组；完整 `session_plan` 的 `items` 才是按顺序执行的一次训练。
- 生成动作草稿只保存聊天中的草稿元信息；**不会创建个人动作库记录**。
- 生成或采用计划不等于创建训练记录。App 负责添加动作、创建训练、记录完成情况并同步。
- 高把握的动作名称纠错会先按理解继续建档并说明纠正；有实质歧义时才追问。
- 这些流程都可以通过继续对话调整；失败轮次不能当作已完成结果。

## 2. 接入顺序与身份

建议按以下顺序接入：身份 → 普通聊天/历史 → 动作卡 → 分块复盘 → 自定义草稿 → 完整计划。
先用本目录样例完成 UI，再连接真实服务。

首次安装持久保存 `installationId`，然后：

```http
POST /v1/users/bootstrap
Content-Type: application/json

{"installationId":"persistent-app-installation-id","displayName":"测试用户"}
```

返回 `{userId, displayName, authEnabled}`，`userId` 是服务器分配的内部 ID。
重复 bootstrap 同一安装会复用用户。已有训练来源迁移时，安装标识应与原 `sourceId` 对齐。

| 模式 | 每个 `/v1/*` 请求的身份 |
| --- | --- |
| 开发环境 `AUTH_ENABLED=false` | `X-User-Id: <bootstrap 返回的 userId>`；不要依赖无请求头的兼容默认用户 |
| `AUTH_ENABLED=true` | `Authorization: Bearer <token>`，**包括 bootstrap**；可选 `X-User-Id` 必须与 token 用户一致 |

`GET /v1/me` 可检查当前身份。当前服务提供管理员签发 token 的脚本，没有 App 登录/刷新 token API；
正式账号登录接入需另行完成，不能把开发环境用户头当成认证。
切换账号时清理旧账号请求、对话、图片和资源缓存。外部相对 URL 均以 API base URL 解析；
图片和头像请求也携带身份。不要把模型供应商密钥放进 App。

## 3. 聊天请求

`POST /v1/chat`，请求 JSON，返回 `application/x-ndjson`。
客户端为会话和每次请求生成 UUID，便于重试识别。

```json
{
  "conversation_id": "11111111-1111-4111-8111-111111111111",
  "request_id": "22222222-2222-4222-8222-222222222222",
  "message": "帮我分析历史训练记录",
  "images": [],
  "context": {
    "active_workout": null,
    "recent_exercises": [],
    "available_equipment": [],
    "review_options": {
      "scope": "multiple",
      "selection_mode": "recent_workouts",
      "workout_count": 7,
      "time_zone": "Asia/Shanghai"
    }
  }
}
```

`message` 去首尾空格后最多 4000 字符，`images` 最多 3 个已上传图片 ID；至少有文字或图片。
`active_workout` 可为 `{id, status, exercise_ids}`，状态为 `active / paused / completed / discarded`。
修改已有计划时传 `context.plan_to_modify`（计划 UUID）。

`history`、`previous_result_ids` 是兼容字段，HTTP 层忽略，App 不必发送。
服务端从数据库读取成功对话历史、复盘事实、训练偏好、计划、动作草稿和训练历史；
不要自行写入 `training_review / training_profile / session_plans / custom_exercise_drafts / workout_history`。
当前训练快照不能替代历史训练同步。

## 4. 流式协议与状态机

这是 **UTF-8 NDJSON，不是 SSE**：每行一个 JSON 对象，没有 `data:` 前缀。
网络 chunk 可能包含半个汉字、半行或多行。必须先增量解码 UTF-8，再按行解析。
Flutter 可用 `byteStream.transform(utf8.decoder).transform(const LineSplitter())`；
同时处理 HTTP 非 2xx、解析错误、取消和没有终止事件的 EOF。

所有事件带 `type / request_id / conversation_id / sequence`，`sequence` 从 1 开始。
按请求隔离状态；它不是服务器断点续传游标。

| `type` | 主要字段 | App 动作 |
| --- | --- | --- |
| `status` | `stage` | 更新进度：`understanding / retrieving / responding` |
| `trace` | `step: {stage,title,summary,data}` | 可选展示处理摘要；不是模型内部思维链 |
| `text_delta` | `delta` | 普通文本追加；复盘 v1 分块模式下不再重复显示它 |
| `exercise_cards` | `kind,result_id,cards` | 暂存/更新该结果的卡片 |
| `report_layout` | `schema_version,outline,artifacts,datasets,facts` | 注册复盘资源与顺序，切换为分块渲染 |
| `block_start` | `block: {id,type:"markdown",content,parts}` | 创建正文块 |
| `block_delta` | `block_id,delta,parts` | 向该块追加内容，`delta` 和 `parts` 二选一渲染 |
| `block_end` | `block_id` | 标记该正文块结束 |
| `block` | `block: {id,type:"chart_ref",artifact_id}` | 在该位置呈现图表 |
| `done` | `response`，可能有 `replayed:true` | 用完整返回替换临时状态，完成本轮 |
| `error` | `code,message` | 终止本轮，保留部分内容并显示失败 |

正文、卡片、资源以 **`done.response` 为最终快照**。不要将最终正文或卡片再次追加一遍。
请求数据库提交完成后才发送 `done`；HTTP 200 只表示开始接收流，不代表成功。

复盘按校验后的块增量交付，不保证每个模型 token 都立即显示。计划可先流出构思，
最终用完整方案替换；完整处方到达前不应开放执行操作。自定义动作也不承诺逐 token 草稿输出。
outline 可以先创建加载占位，但没有收到的块不能标成已生成。

建议客户端状态：`sending → streaming → completed`，另有 `failed / cancelled`。
复盘即使收到 `done`，也要根据 `review.omitted_findings` 表达分析缺失，不能把所有内容都视为完整。

重试规则：

- 同一已完成 `request_id` + 相同输入：返回单个 `done`，`replayed:true`；没有之前的增量事件也必须正常显示。
- 同一 ID 改变输入、该请求仍在生成、同一会话并发生成：409。
- 已明确失败后重试：使用新 `request_id`，保留原会话 ID。
- 断网而不知道是否完成：查询历史，或用原 ID 和原请求体重试取得已完成结果；409 时先等原请求结束。
- 当前没有按 sequence 恢复一半流的 API。主动停止时关闭请求，历史可能保留部分结果。

## 5. 最终响应与 ID

基础响应有 `conversation_id / result_id / kind / text / cards / catalog_version`。
其他字段如下；完整样例见配套 JSON 的 `scenarios.<场景>.response`。

| 字段 | 用途 |
| --- | --- |
| `trace` | 可持久显示的处理摘要数组 |
| `suggestion_kind` | `single_exercise / alternatives / session_plan / null` |
| `schema_version` | 复盘分块协议目前为 `1`；其他回复通常 `null` |
| `outline, blocks` | 规划顺序、已经生成的有序正文/图表块 |
| `artifacts, datasets, facts` | 图表定义、JSON 数据、事实字面值的独立注册表 |
| `review` | 结构化复盘事实、证据、局限与分析状态 |
| `session_plan` | 完整计划或 `null` |
| `plan_preview` | 待完善/阻塞预览或 `null` |
| `custom_exercise` | 自定义动作追问、已有动作或草稿结果 |

外层字段通常 **snake_case**，嵌入 `exercise` 对象保持 **camelCase**。
训练同步对象、用户 bootstrap 对象也是 camelCase，不能给整个响应做全局自动改名。
旧历史可能缺少新增字段；数组/注册表缺失按空处理。遇到未知类型保留文本与可识别附件。

所有 ID 都按不透明字符串保存，不转换为数字：

| ID | 当前形式及用途 |
| --- | --- |
| 会话、请求、结果、计划、revision/event ID | UUID；不同语义不要混用 |
| `draft_id` | 来源请求的 UUID；不是保存后的动作 ID |
| 自定义动作 ID | `cx_` + 8 位小写十六进制 |
| 复盘块/图表/数据/事实 | `block_ / chart_ / data_ / fact_` + 8 位小写十六进制 |
| 上传图片 ID | 64 位十六进制内容摘要 |

短资源 ID 在一份报告内唯一，缓存键使用 `(result_id, resource_id)`。
兼容旧报告的更长 ID，不在客户端强制拒绝非 8 位后缀。

## 6. 动作卡与逐组处方

`cards[]` 的字段是：`exercise, equipment_label, body_part_label, context,
can_add_to_active_workout, set_prescriptions`。
`exercise` 包含：

```text
id, sourceName, displayNameZhHans, aliasesZhHans,
bodyPartCode, equipmentCode, targetMuscleCode, secondaryMuscleCodes,
instructionStepsZhHans, mediaId, detailType, defaultWeightUnit,
isActive, catalogVersion, createdAt, updatedAt, sourceKind, trainingType, avatar
```

`sourceKind` 为 `standard / custom`。`detailType` 为 `weightedReps / bodyweightReps /
duration / machineLevelReps`，决定输入重量+次数、自重次数、时长、档位+次数。
标准动作可通过 `GET /v1/exercises/{id}/thumbnail` 获取 JPEG；缺图显示占位。
自定义动作头像为 SVG，也可依据 `avatar.key` 映射 App 内置图标。

推荐的 `set_prescriptions` 与计划中同名字段使用相同结构，一项就是一组：

```json
{
  "kind": "working",
  "repetitions": 8,
  "duration_seconds": null,
  "rest_seconds": 120,
  "target_rir_min": 2,
  "target_rir_max": 3,
  "load": {
    "strategy": "trial", "value": null, "unit": "kg", "convention": null,
    "explanation": "重量待试做确认。", "observation_id": null
  }
}
```

- `kind` 为 `warmup / working`；次数与时长二选一，与动作记录类型一致。
- `load.strategy`：`trial / history / trial_increment / user / bodyweight / none`。
- `load.unit`：`kg / lb / level / null`；口径 `convention`：`total / per_hand / added /
  assistance / bodyweight / machine_stack / machine_level / unknown / null`。
- 未知重量保持 `null`，不能显示成 0 kg；单位和负重口径必须保留，不能无条件合并或转移给另一动作。
- 使用逐组列表生成训练草稿。旧汇总 `sets` 指工作组，`total_sets` 包含热身组，不能重复展开两遍。
- 用户编辑范围：次数 1–1000，时长 1–3600 秒，休息 0–900 秒，目标 RIR 2–6；模型建议范围更窄。

`can_add_to_active_workout` 只是生成时的提示。用户点击“添加”时，App 再检查当前训练状态、
动作是否重复/有效，然后创建**未完成**的训练条目。处方是目标，不是已完成训练或实际用力反馈。
计划预览的卡片不可执行；不要仅凭 `cards.length > 0` 显示“开始训练”。

## 7. 复盘与跨端图表

### 选取训练

`context.review_options` 控制范围，也可通过自然语言表达。`GET /v1/training/review` 使用相同选项，
但它返回统计事实，**不会生成完整聊天文章**。

- 多次默认：`scope:"multiple", selection_mode:"recent_workouts", workout_count:7`。
- 单次：`scope:"single"` 默认最近一次；指定时同时传 `source_id`、`workout_id`。指定不存在返回 404。
- 日期范围：显式 `selection_mode:"date_window"`，并传 `recent_days / baseline_days / end_date`。
  `end_date` 是所选时区的排他日期，`null` 表示包含截至查询时已同步的当天记录。
- 使用 IANA `time_zone`，默认 UTC。不要习惯性发送 `recent_days:7` 代替最近 7 次；
  兼容逻辑会将未指定 selection_mode 的显式日期选项解释为日期窗口。

单次关注记录中的动作顺序、逐组变化、部位和时长；多次关注频率、间隔、部位覆盖与已确认周期。
未记录的真实组间/动作间休息保持未知，录入时间差不等于休息。待归类动作不能算作零训练量。

### 输出框架

```text
outline: [正文 A, 图表 A, 正文 B, 图表 B]
blocks:  [markdown, chart_ref, markdown, chart_ref]
                         │
                         └─ artifact_id → artifacts[id] → dataset_id → datasets[id]
```

按 `blocks` 顺序渲染已生成内容；`outline` 用于规划、占位与校验顺序。
相邻文本已由服务端按结构组织，客户端不必再用正则从正文切出图表 ID。
`text` 是供兼容和复制使用的已展开正文，不含图表定位标记；只渲染 text 会丢失图表位置。

正文块：`{id,type:"markdown",content,parts}`。`content` 为已解析事实的兼容 Markdown。
支持结构化 inline 的客户端可使用 `parts`：

```json
[
  {"type":"text","text":"本次记录包含 "},
  {"type":"fact_ref","fact_id":"fact_1234abcd"},
  {"type":"text","text":"。"}
]
```

`facts[fact_id]` 含 `value / evidence_id / source_key`；`value` 是已格式化的字面字符串。
将 fact_ref 作为字面文本节点插入；若拼接 Markdown，先转义 Markdown 特殊字符。
同一块的 `parts` 和 `content` 不要同时显示；不要把模型内部引用语法暴露给用户。

图表块 `{id,type:"chart_ref",artifact_id}` 对应：

```json
{
  "format": "chart-spec/v1",
  "chart_type": "bars",
  "title": "训练部位分布",
  "description": "按目录主部位统计已完成组",
  "unit": "组",
  "dataset_id": "data_1234abcd",
  "encoding": {"label":"label","value":"value"},
  "truncated": false
}
```

对应 `datasets.data_1234abcd`：

```json
{"rows":[{"label":"腿部","value":12},{"label":"背部","value":9}]}
```

实际数据行还可能含 `at`（ISO 时间）和 `source:{source_id,workout_id,version}`。
目前支持的图表只有：

| `chart_type` | App 绘制要求 |
| --- | --- |
| `timeline` | 使用 `encoding.at` 对应时间决定位置，保留真实日期间隔，不能把每次训练等距排列 |
| `columns` | 竖向柱状图；通常展示每次完成组数，数值轴从 0 开始 |
| `bars` | 横向条形图；通常展示部位分布，数值轴从 0 开始 |

显示标题、说明、单位，支持点按/聚焦读取数值，并提供可展开的数据表。
时间 label 已按报告时区生成；不要自动换时区后造成 label 与数据不一致。
`truncated` 为 true 时提示数据被截断。未知格式或引用缺失时保留正文，能获取数据则展示表格降级。

**当前格式是图表规范 + 内联 JSON 数据，不是 HTML5、图片、ECharts option 或 JavaScript。**
目前没有独立数据文件下载接口；artifacts/datasets 通过流式布局和最终响应的额外字段传输。
Flutter 可原生绘图，Web 可转成 SVG；若选 WebView，需要 App 自带受控渲染页面，不执行模型 HTML。

v1 分块生效后，不再额外渲染整段 `text` 或把旧 `review.charts` 追加到底部，否则重复。
旧历史未带 schema_version 时使用兼容正文/结构化复盘；未知 schema_version 降级显示文本。

## 8. 自定义动作：编辑与确认保存

`kind:"custom_exercise"` 下读取 `custom_exercise.status`：

| 状态 | 返回内容 | App 行为 |
| --- | --- | --- |
| `needs_input` | `question` | 展示追问，继续当前会话 |
| `existing` | 已有 `exercise`，通常也有动作卡 | 展示已有动作，不创建重复草稿 |
| `draft` | `draft_id,version,definition,avatar,labels` | 展示“尚未保存”、编辑、确认保存 |

`name_correction:{input_name,resolved_name}` 在发生纠错时提供。
`definition` 是 snake_case 元信息，不是标准目录的 camelCase exercise：

```text
name, aliases, detail_type, body_part_code, equipment_code,
target_muscle_code, secondary_muscle_codes, training_type,
instructions, default_weight_unit, inferred_fields
```

`name` 与 `detail_type` 必填；`training_type` 为 `strength / cardio / mobility / stretching / other`。
动作名 1–120 字符，别名最多 10 个、步骤最多 10 条（每条最多 500 字符）；分类使用服务器代码。
`inferred_fields` 用于标识助手补全的字段。选择项来自 `GET /v1/custom-exercises/options`，
返回 `terms` 与 `training_types`；显示中文标签，提交 code，不由中文标签反猜 code。

头像结构为 `{kind:"builtin",key:"mobility",url:"/v1/custom-exercises/avatars/mobility"}`。
这是按训练类型分配的内置 SVG，当前不是每个动作独立生成的插画。
Flutter 可映射 key 到本地图标，或通过支持 SVG 的组件携带身份加载 URL。

编辑并保存流程：

1. 展开草稿编辑器，允许修改名称、别名、分类、器械、肌群、记录类型、步骤等。
2. `PUT /v1/custom-exercises/drafts/{draft_id}`，请求 `{"version":当前版本,"exercise":完整definition}`。
   这是完整定义替换，不是 JSON Patch。返回更新的草稿及递增 version，更新 UI 与头像/标签。
3. 用户点击“确认保存”后，`POST /v1/custom-exercises/drafts/{draft_id}/save`，请求 `{"version":最新版本}`。
4. 返回 `status:"created"|"existing"` 和 `exercise` 等保存结果，改为已保存状态。
   此后使用返回的真实动作 ID，不把 draft_id 当作 exerciseId。

编辑只更新聊天草稿；确认保存才写个人动作库。同版本重复保存返回已有结果；版本冲突 409 时重新读取历史。
已保存草稿不能再编辑，改走已保存动作的编辑接口。
历史中的 `custom_exercise.saved_result` 可恢复保存状态；不要只根据原始 assistant 文案判断。

个人库管理接口：

| 接口 | 用途 |
| --- | --- |
| `GET /v1/custom-exercises?include_archived=false` | 当前用户个人动作 |
| `GET /v1/custom-exercises/{id}` | 详情、definition 和版本 |
| `POST /v1/custom-exercises` | 独立手动建档，`{exercise:definition}`；也应由用户明确保存触发 |
| `PUT /v1/custom-exercises/{id}` | `{version,exercise:definition}`；保存后不能更改 detail_type |
| `DELETE /v1/custom-exercises/{id}?version=N` | 归档，不删除历史训练 |

已保存个人动作参与该用户的查找、推荐与复盘；标准库不变，不会自动改写旧训练的动作关联。

## 9. 完整计划、预览和生命周期

`kind:"session_plan"` 不一定有可执行方案：

- `plan_preview != null`：`status:"needs_input"|"blocked"`，含 `theme,next_question,target_date`。
  可能有最多 10 张按顺序的候选卡，`set_prescriptions` 为空、不可添加；没有保存完整计划。
- `session_plan != null` 且 `suggestion_kind:"session_plan"`：有经过校验并持久化的方案。
  无动作的休息/调整方案不能直接开启训练。

完整计划包含 `id,version,status,theme,rationale,items`，以及时间预算、准备/结束时间、
调整依据、profile_snapshot、证据和限制等。`items[]` 是顺序列表，嵌入真实 `exercise`、
`set_prescriptions`、工作组/总组汇总及用时/选重说明。配套 JSON 有完整实际响应。
使用逐组处方显示/编辑，保留热身和工作组区别，不从正文提取组数或重量。

生成结果中的 `session_plan.status` 是当时快照。当前状态以以下接口为准：

```http
GET /v1/training/plans/{plan_id}
```

返回 `{plan,status,revision,created_at,superseded_by,events,events_truncated,execution}`。
注意 `plan.version` 是方案版本，外层 `revision` 是乐观并发版本，写事件时使用后者。
`GET /v1/training/plans` 返回最近计划上下文（最多 5 份），不是完整可分页计划历史。

采用计划的请求示例：

```http
POST /v1/training/plans/{plan_id}/events
Content-Type: application/json

{"event_id":"33333333-3333-4333-8333-333333333333","expected_revision":0,"kind":"accepted"}
```

每次成功写入后使用返回的新 revision。相同事件 ID/内容可幂等重试。

| 当前状态 | 可提交的 `kind` |
| --- | --- |
| `proposed` | `accepted / skipped / feedback` |
| `accepted` | `executed / skipped / feedback` |
| `executed`、`skipped` | `feedback` |

`skipped` 需 `reason`；`executed` 需 `workout:{source_id,workout_id}`，关联当前用户已同步的已完成训练。
`feedback` 可附 reason、effort；完整约束见 [训练 schema](../../src/fitness_bot/training_schemas.py)。
采用仅更新计划状态；App 的“开始训练”仍需创建本地未完成训练，再通过训练同步上传。

用户直接编辑顺序/删减动作/逐组目标：

```text
POST /v1/training/plans/{id}/revision
{revision_id: 新UUID, expected_revision: 当前revision,
 items: [{exercise_id: 原方案动作ID, set_prescriptions: 完整逐组列表}]}
```

返回新计划，ID 为 revision_id，状态 proposed；原方案被标记 superseded_by。
后续采用新 ID。替换成新动作通过聊天重新生成（`context.plan_to_modify`）；
不要把旧动作负重直接转移到替代动作。冲突 409 时读取最新状态再操作。

训练偏好：`GET /v1/training/profile` 读取，`PUT` 以 `{expected_version,profile}` 显式整体更新。
主要字段为 goal、experience、priority_exercise_ids、weekly_sessions、session_minutes、equipment、
time_zone、readiness、confirmed_cycle。readiness 含 observed_at 和 pain/fatigue/sleep；
咨询无需先填表，具体量化计划才检查条件。聊天临时条件不等同于永久保存偏好。
器械列表来自 `GET /v1/training/options`。

## 10. 历史、图片和训练数据

| 接口 | 关键约定 |
| --- | --- |
| `GET /v1/conversations?limit=50&offset=0` | 会话列表 |
| `GET /v1/conversations/{id}/turns?after_id=0&limit=100` | 按数值轮次 ID 分页，不是 request_id |
| `POST /v1/images` | 原始二进制请求体，Content-Type 为 JPEG/PNG/WebP；不是 multipart |
| `GET /v1/images/{id}` | 携带身份读取图片 |
| `GET /v1/exercises?q=...&equipment=...&body_part=...&target_muscle=...&limit=20` | 查真实标准库及当前用户个人库；limit 1–100 |
| `GET /v1/exercises/{id}` | 完整目录动作 |
| `GET /v1/training/completed-workouts` | 最近已完成训练的选择列表，最多 50 条 |
| `PUT /v1/workouts/{id}` | App 上传完整版本化训练聚合；不是聊天生成动作 |
| `GET /v1/workouts`、`GET /v1/workouts/{id}?sourceId=...` | 查询当前用户已同步记录 |
| `PUT /v1/training/observations/{id}/attestation` | 可选：确认负重口径、动作条件和实际余力；不改原始训练 |

上传图片最多 5 MiB、2000 万像素，静态 JPEG/PNG/WebP；不支持 HEIC/GIF/动画。
成功 201 返回 `{id,url,mime_type,width,height,size_bytes}`，随后把 ID 加到聊天 images。

历史轮次有 `id,request_id,user_text,images,assistant_text,context,response,status,error_code,created_at,updated_at`。
status 为 `completed / failed / cancelled / generating`，以外层状态表达整轮完成情况。
渲染优先使用 response；旧记录无 response 时回退 assistant_text。
失败/取消的复盘可能带 `response.incomplete:true` 和部分 blocks/resources，**不保证包含完整 ChatResponse 必填字段**。
客户端应使用独立的历史部分结果模型，不能因缺 result_id/catalog_version 丢弃已有图文。
没有收到的块不补造成完整分析。`review.omitted_findings > 0` 应显示分析有缺失。

训练同步请求是 camelCase，包含 `sourceId,version,deleted,workout`；
见 [完整合成上传样例](../../tests/fixtures/workout_upload.json) 与 [训练同步 schema](../../src/fitness_bot/workout_schemas.py)。
同版本同内容可重放，旧版本或同版本不同内容返回 409；删除用更高版本墓碑 `deleted:true,workout:null`。
收到成功回执后 App 才标记同步完成。复盘使用当前用户已同步、未删除、已完成的训练及完成组。
未同步的本地记录不会凭空参与复盘，模型也不会替 App 修改训练。

## 11. 错误处理与验收

HTTP 错误为 FastAPI `detail`（可能为字符串或校验错误数组）：
401 身份失效，403 身份不一致，404 不存在/无权读取，409 版本或并发冲突，
413 图片过大，415 图片格式不支持，422 参数校验失败，503 模型/存储不可用。
流内 `error.code` 当前包括 `timeout / generation_failed`；不要显示内部异常或敏感请求头。

App 交付前至少覆盖：

- [ ] 六种 kind 和 plan_preview 均可显示；推荐备选与整次计划的操作区别正确。
- [ ] UTF-8 跨 chunk、多事件同 chunk、只收到重放 done、断流、取消、409 重试正常。
- [ ] done 替换临时结果，正文/卡片/图表不重复，旧历史和部分历史可恢复。
- [ ] 复盘正文与图表交错，时间线保留实际间隔，柱图从零开始，数值与数据表一致。
- [ ] 报告资源按结果隔离；未知格式、缺失引用、空数据能降级。
- [ ] 默认分析最近 7 次；单次指定记录、日期窗口及时区边界能正确请求。
- [ ] 自定义动作可编辑，纠错后的名称正确；生成/编辑不会写个人库，确认保存才创建。
- [ ] 草稿保存重试不重复建档，版本冲突不覆盖他人修改，历史能恢复已保存状态。
- [ ] 头像支持 SVG 或 key 原生映射；所有媒体请求带身份，标准动作缺图有占位。
- [ ] 计划预览不能执行，完整处方保留每组及单位/口径；采用不自动创建完成训练。
- [ ] 账号切换隔离数据；真机 base URL 可达；模型密钥不进入客户端。

建议复用实现作为行为参考：[Web 流事件处理](../../src/fitness_bot/web/stream.mjs)、
[Web 分块状态处理](../../src/fitness_bot/web/content-blocks.mjs)。
服务端权威定义：[Chat schema](../../src/fitness_bot/schemas.py)、
[自定义动作 schema](../../src/fitness_bot/custom_exercise_schemas.py)、
[逐组处方](../../src/fitness_bot/prescriptions.py)、[报告交付](../../src/fitness_bot/report_delivery.py)。
Playground 仅是协议的一个消费者，不是 App 必须嵌入的页面。
