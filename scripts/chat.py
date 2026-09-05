"""Stream a chat response: uv run python scripts/chat.py '找两个哑铃胸部动作'."""

import argparse
import asyncio
import json

import httpx


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    parser.add_argument("--url", default="http://127.0.0.1:8000/v1/chat")
    args = parser.parse_args()
    terminal = False
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", args.url, json={"message": args.message}) as response:
            if response.is_error:
                await response.aread()
                raise SystemExit(f"HTTP {response.status_code}: {response.text}")
            async for line in response.aiter_lines():
                if not line:
                    continue
                event = json.loads(line)
                match event["type"]:
                    case "text_delta":
                        print(event["delta"], end="", flush=True)
                    case "exercise_cards":
                        print("\n" + json.dumps(event["cards"], ensure_ascii=False, indent=2))
                    case "done":
                        terminal = True
                        print(f"\nconversation_id={event['conversation_id']}")
                    case "error":
                        raise SystemExit(f"\n{event['code']}: {event['message']}")
    if not terminal:
        raise SystemExit("Stream ended without done; retry the request.")


if __name__ == "__main__":
    asyncio.run(main())
