# 当前运动与下次运动计划协议

状态：后端与 App 已实现，通过真实 HTTP 协议及 App 本地持久化联调。沿用 `/v1/chat` NDJSON、计划快照、revision 和 events 接口。

## 目标由 App 明确选择

`context.planning_target` 为 `current_workout`（补全本次）或 `next_workout`（规划下次）。未传时保持旧客户端的下次计划语义；不能因为存在 `active_workout` 就自动向当前运动添加计划。

```json
{
  "message": "结合已完成的内容，补全本次运动",
  "request_id": "客户端生成的 UUID",
  "conversation_id": "客户端生成的 UUID",
  "context": {
    "planning_target": "current_workout",
    "remaining_minutes": 30,
    "current_workout": {
      "sourceId": "设备安装标识",
      "version": 12,
      "deleted": false,
      "workout": "与运动同步接口一致的完整 WorkoutSnapshot 对象"
    }
  }
}
```

示例中的说明字符串不是真实有效载荷。`workout` 必须使用现有运动同步协议的完整对象，包含 active/paused 状态、时间、record/set ID、每组负荷及完成状态；引用关系必须一致。App 在发送前保存编辑器，再以一个数据库事务读取版本和快照。`remaining_minutes` 表示用户愿意用于**新增安排**的时间，包含新增安排自身的准备、休息、收尾与缓冲，不估算或扣除已有待完成内容。缺失时先询问，不生成可执行本次计划。

下次计划仅发 `planning_target: next_workout`。模型只使用服务端已完成历史、用户档案和本次明确输入；当前未完成运动不作为历史。对话后续可恢复明确的计划目标和日期，但不能偷偷复用旧的当前运动快照。App 每次本次计划请求都刷新快照；请求重试则复用原 request_id 和原始完整请求。

## 返回：已有记录与新增处方分离

`plan_preview` 与 `session_plan` 均携带 `planning_target`；本次计划携带原 `current_workout`。现有 `items` 始终表示**新增动作处方**，每项沿用 `set_prescriptions`（组别、负荷/选重方式、次数/时长、休息、余力）和推荐理由。已有动作不能再次出现在新增 items 中。

完整本次方案由 App 展示为：已有内容（逐记录保留，含完成/待完成状态）→ 新增安排。已有快照不可通过计划编辑修改，不转成模型处方，也不合并重复动作记录。模型应结合已做与待做的训练量判断是否还需要补充。

`outcome` 为 `add_exercises`、`nothing_to_add` 或 `rest`。后两种没有添加按钮，不删除、结束或覆盖当前运动。`working_sets` 与时间估计仍仅统计新增处方；`existing_completed_sets` 和 `existing_pending_sets` 单独展示，不把原始记录组数冒充正式组数。

预览仍是待补充候选，不能确认。模型提供的动作、器械、剂量、历史负荷依据继续通过程序校验。没有可比历史负荷时展示试做选重及依据，不能编造公斤数。

## 确认与状态流转

### 补全本次

1. App 展示完整已有内容与新增安排，确认按钮明确新增动作/组数。
2. 服务端按既有 revision / accepted event 保存用户确认；服务端不写运动数据。
3. App 在本地事务内校验 sourceId、workout.id、version 和当前 active/paused 状态；不一致则提示按最新记录重新生成。
4. 仅追加新增动作和未完成组；原记录/组/完成状态不变。plan ID 作为应用幂等键，双击、重试、重启不得重复添加。
5. 本次计划不能进入“下次运动计划”列表。服务端恢复 accepted 状态不能自动写入任何运动。

### 规划下次

1. 用户确认后存为“待开始”计划；当前有运动时也允许保存。
2. 运动页展示计划主题、动作与组数、逐组处方、推荐依据及开始入口。
3. 无当前运动时，开始操作在一个事务内创建运动、复制未完成处方、标记计划已使用；重复点击只返回同一运动。
4. 当前已有 active/paused 运动时保留计划并说明结束后可开始，不替换当前运动。

计划仍使用不可变版本。修订只修改新增 items，保留目标与快照关联。执行事件若关联本次计划，必须关联原 sourceId/workout.id。实际执行数据仍由独立同步协议上传；计划的“确认/已添加”和“训练已完成”不得混淆。

## 验收

- 真实 HTTP NDJSON：两类目标、已有完成/未完成组、候选补问、无须新增、持久化先于 done。
- 下次计划模型输入不包含进行中快照；本次新增不能重复已有动作。
- 修订保持已有快照；重试载荷与原快照绑定；错误 workout 执行链接被拒绝。
- App 原子追加保留已有记录；过期/跨设备/结束状态拒绝；重复确认不重复添加。
- 待开始计划重启后可恢复；当前有运动时不能开始；开始只消费一次。

## 后端验证（2026-09-18）

`uv run pytest -q`：344 项通过；`uv run ruff check .`、`uv run ruff format --check .` 通过。实际 loopback HTTP 用例位于 `tests/test_workout_plan_http_smoke.py`，四类响应导出至 App 的 `test/fixtures/workout_plan_wire/`。所有自动测试使用注入模型与隔离 SQLite，没有真实模型调用。


## App 验证（2026-09-19）

Flutter 全量回归 505 项通过、2 项原有跳过，静态分析无问题；另有实际 Flutter 客户端 → 隔离 HTTP 后端 → Drift 生命周期测试 1 项通过，覆盖本次追加、原记录保留、幂等确认、下次保存、当前运动阻止开启及结束后只开启一次。该跨仓库测试需 `FITNESS_LIVE_PLAN_HTTP=1` 启用，默认跳过，使用注入模型和临时 SQLite。

待开始计划和本次应用回执由 App 本地持久化管理；服务端 accepted 状态恢复不会自动向本次运动追加动作。模拟器已构建运行；锁屏限制了最后的手动点击检查，页面入口与流转由路由/widget 测试覆盖。本轮自动联调不验证真实 LLM 输出质量。

## 单轮完整候选计划（2026-09-22）

明确请求整次训练时，一轮返回完整 `session_plan`，不因未填写目标、经验、时长、器械或当天状态而退回多步问卷。本轮明确输入 > 本会话明确条件 > 已保存资料 > 历史/标明的默认值。本会话条件只来自服务端解析记录，不信任客户端提供的档案；推断不写入长期档案。器械优先参考历史，其他常见器械暂按可用，用户明确限制后严格遵守。动作数量按历史训练量和时间分配，不固定为四项。

`planning_assumptions` 为 `{field, source, value, explanation}` 数组，标明历史推断、默认值或未知状态；App 在概览及详情显示。`planning_history_summary` 描述近期样本的动作数、完成组数和熟悉器械，供模型分配训练量。未知状态不会被补成“正常”，也不能据此获得自动进阶资格；明确疼痛仍阻止生成负重方案。补全本次训练仍需要最新 App 快照及明确的新增时间预算。

逐组 `load.strategy` 新增 `history_reference`：同动作已完成记录中的原始数值、单位、`observation_id` 和说明；未核实的 `convention` 保留 `unknown`，附加/辅助负荷保留 `added`/`assistance`。它不同于已确认可比记录，不授权自动递增，也不能跨动作搬用。没有有效同动作数据则展示待试重。生成、修订和首次确认会核对每个引用组的原始快照版本；历史变化返回 409，已成功操作的相同请求仍幂等重放。

调整次数、休息、顺序时可保留原始参考负荷，无需再逐组确认。改动重量、单位或口径后转为 `user` 且清除旧来源；不得伪造 `history_reference`。聊天卡片和完整详情都显示“确认并保存计划”，只在点击后成为正式待开始计划；生成、编辑、保存均不自动开始训练。

### Three phases and eight purpose roles (2026-09-22)

New complete plans use `semantics_version: 1` with occurrence `item_id`, `phase`, and `purpose_role` across the complete ordered `items` list. Activity rows use `kind: activity` and do not count as strength working sets. Read [plan-phase-roles.md](plan-phase-roles.md) for the full mapping, phase completeness, time accounting, editing and legacy rules; [the synthetic example](examples/phase-plan.json) is shared with Flutter parsing tests. Existing save/start separation is unchanged.
