# 真实 Chat 接口快速测试 — 2026-09-05

使用已有 `.env` 的 OpenAI-compatible API 配置，通过真实本机 HTTP 调用 `/v1/chat`。
请求模型为 `gpt-5.4-mini`，`OPENAI_REASONING_EFFORT=low`；测试输入为合成数据。
未修改已有 `.env`；模型选择和推理强度仅通过启动进程的环境变量指定。

## 最终实测

| 场景 | 首段文本 | 全部完成 | 文本增量数 | 动作卡 |
| --- | --- | --- | --- | --- |
| search | 3.720s | 4.141s | 60 | 哑铃环绕直臂上拉、哑铃卧推 |
| recommendation | 2.791s | 2.795s | 65 | 四分之三仰卧起坐、45度侧屈、空中自行车 |

两条请求均为 HTTP 200，正常以 done 结束。断言通过：动作数、部位、器械、
文本增量与最终文本一致，以及文本动作顺序与卡片顺序一致。前一轮另外验证了事件序号、
request_id 一致性、目录 ID、活动训练按钮和器械上下文过滤。
这是少量冒烟样本的客户端端到端耗时，包含网关和模型延迟，不代表性能基准或稳定 SLA。
推荐样本的增量几乎同时到达，不能据此保证上游链路始终逐 token 即时传输。

## 实测发现与修复

首次核心推荐返回空结果。复现的检索条件为 body_part=waist、equipment=body weight、
target_muscle=core；目录中 waist 动作的主要肌群为 abs，没有主要肌群为 core 的动作。
移除 target_muscle 条件后可检索到结果。原因是将包含次要肌群的完整术语表交给了模型。
现只提供在活动动作主要筛选字段中实际存在的术语，保留完整本地知识库不变；
同时明确核心部位映射，避免添加未请求的肌群条件。新增回归测试固定这一边界。

首次搜索还出现文字列表与卡片顺序不同，已补充顺序提示并在最终实测中断言顺序一致。
提示约束不等于所有未来模型回答均正确；这里仅报告样本结果。

## 验证与复现

20 项自动测试通过，ruff check 和 format 检查通过。第三方 Starlette/AnyIO 有一条弃用提示。
自动测试仍不调用真实模型；本次真实测试为手动执行。

```bash
OPENAI_MODEL=gpt-5.4-mini OPENAI_REASONING_EFFORT=low uv run fitness-bot
uv run python scripts/chat.py '找两个哑铃胸部动作'
uv run python scripts/chat.py '推荐三个徒手核心动作，我没有训练历史。'
```
