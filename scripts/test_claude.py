"""Quick test that claude-agent-sdk streaming works."""
import asyncio
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def one_turn(client, prompt, label):
    from claude_agent_sdk import AssistantMessage, TextBlock, StreamEvent
    t0 = time.perf_counter()
    await client.query(prompt)
    first_delta_t = None
    streamed_chunks = []
    final_text = ""
    async for message in client.receive_response():
        if isinstance(message, StreamEvent):
            ev = message.event or {}
            if ev.get("type") == "content_block_delta":
                d = ev.get("delta", {})
                if d.get("type") == "text_delta":
                    if first_delta_t is None:
                        first_delta_t = time.perf_counter()
                    streamed_chunks.append(d.get("text", ""))
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    final_text += block.text
    total = time.perf_counter() - t0
    ttft = (first_delta_t - t0) if first_delta_t else None
    text = "".join(streamed_chunks).strip() or final_text.strip()
    print(f"[{label}]  total={total:.2f}s  first_delta={ttft:.2f}s" if ttft else f"[{label}]  total={total:.2f}s  (no streaming)")
    print(f"    {text}\n")


async def main():
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    opts = ClaudeAgentOptions(
        system_prompt=(
            "You are Priya, a customer relationship officer at FlexiLoans. "
            "Speak naturally, 1-2 sentences only, like a phone call. "
            "You are calling Rajesh about his upcoming EMI."
        ),
        model="claude-sonnet-4-5",
        allowed_tools=[],
        permission_mode="default",
        max_turns=10,
        include_partial_messages=True,
    )

    print("Connecting...")
    client = ClaudeSDKClient(options=opts)
    await client.__aenter__()
    try:
        await one_turn(client, "[CALL CONNECTED - customer just picked up. Say your opening line.]", "opening")
        await one_turn(client, "Hi this is Rajesh. Yes I'll be able to pay on time.", "turn 2")
        await one_turn(client, "Actually my business is a bit slow this month, can we extend by a few days?", "turn 3")
    finally:
        await client.__aexit__(None, None, None)
        print("Disconnected.")


asyncio.run(main())
