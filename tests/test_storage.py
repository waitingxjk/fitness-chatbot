from uuid import uuid4

import pytest

from fitness_bot.schemas import ChatRequest
from fitness_bot.storage import ConversationStore, TurnConflict


async def test_history_survives_reopening_and_request_is_idempotent(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}"
    store = ConversationStore(url)
    await store.initialize()
    request = ChatRequest(message="找哑铃卧推")
    started = await store.begin_turn(request)
    assert started.request.history == []
    response = {"text": "找到动作", "cards": [{"exercise": {"id": "0289"}}]}
    await store.complete(request.request_id, response)
    await store.close()
    store = ConversationStore(url)
    await store.initialize()
    try:
        replay = await store.begin_turn(request)
        assert replay.cached_response == response
        next_request = ChatRequest(message="第二个呢", conversation_id=request.conversation_id)
        started = await store.begin_turn(next_request)
        assert [m.text for m in started.request.history] == ["找哑铃卧推", "找到动作"]
        assert started.request.previous_result_ids == ["0289"]
        turns = await store.turns(request.conversation_id)
        assert len(turns) == 2
        assert turns[0]["status"] == "completed"
    finally:
        await store.close()


async def test_busy_conversation_and_failed_partial_reply(tmp_path):
    store = ConversationStore(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await store.initialize()
    try:
        request = ChatRequest(message="你好")
        await store.begin_turn(request)
        with pytest.raises(TurnConflict):
            await store.begin_turn(
                ChatRequest(message="并发", conversation_id=request.conversation_id)
            )
        await store.fail(request.request_id, "partial", "generation_failed")
        turns = await store.turns(request.conversation_id)
        assert turns[0]["assistant_text"] == "partial"
        assert turns[0]["status"] == "failed"
        following = await store.begin_turn(
            ChatRequest(message="重试", conversation_id=request.conversation_id)
        )
        assert following.request.history == []
        with pytest.raises(TurnConflict):
            await store.begin_turn(request.model_copy(update={"conversation_id": uuid4()}))
    finally:
        await store.close()
