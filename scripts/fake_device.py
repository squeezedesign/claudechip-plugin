"""Fake Claude Chip: connects to the bridge and prints what the device would get.

  uv run python scripts/fake_device.py --token TOKEN [--port 8765] [--answer allow|deny|ask|none]

--answer controls what happens with permission requests:
  ask   prompt on the terminal (default)   allow / deny   answer automatically
  none  never answer (the terminal prompt in Claude Code must win)
"""

from __future__ import annotations

import argparse
import asyncio
import json

import aiohttp


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--token", required=True)
    p.add_argument("--answer", choices=["ask", "allow", "deny", "none"], default="ask")
    args = p.parse_args()

    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(f"ws://{args.host}:{args.port}/ws") as ws:
            await ws.send_json({"type": "hello", "token": args.token, "device": "fake"})
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    print(f"<< {msg.type} {msg.data} {msg.extra}")
                    break
                data = json.loads(msg.data)
                print("<<", json.dumps(data, ensure_ascii=False))
                if data.get("type") == "permission" and args.answer != "none":
                    if args.answer == "ask":
                        line = await asyncio.to_thread(input, f"allow {data['request_id']}? [y/n] ")
                        allow = line.strip().lower().startswith("y")
                    else:
                        allow = args.answer == "allow"
                    await ws.send_json({"type": "decision", "request_id": data["request_id"],
                                        "allow": allow, "token": args.token})
                    print(">> decision", data["request_id"], allow)


if __name__ == "__main__":
    asyncio.run(main())
