"""Fake Claude Chip: speaks the device protocol and prints what it receives.

  uv run python scripts/fake_device.py [--port 8765] [--answer allow|deny|ask|none]

The first run asks to pair: run 'claudechip-bridge pair <code>' with the code
it prints. The token is kept in scripts/.fake_device_token (git-ignored).

--answer controls permission requests: ask (prompt here, default),
allow / deny (automatic) or none (never answer).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import secrets
from pathlib import Path

import aiohttp

TOKEN_FILE = Path(__file__).with_name(".fake_device_token")
DEVICE_ID = "fake-device"


def sign(token: str, text: str) -> str:
    return hmac.new(bytes.fromhex(token), text.encode(), hashlib.sha256).hexdigest()


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--answer", choices=["ask", "allow", "deny", "none"], default="ask")
    args = p.parse_args()

    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(f"ws://{args.host}:{args.port}/ws") as ws:
            token = TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else None
            bridge_nonce = my_nonce = ""
            seq = 0

            async def auth() -> None:
                nonlocal my_nonce
                my_nonce = secrets.token_hex(8)
                await ws.send_json({"type": "auth", "device": DEVICE_ID, "nonce": my_nonce,
                                    "mac": sign(token, f"auth:{bridge_nonce}:{my_nonce}:{DEVICE_ID}")})

            async def pair() -> None:
                code = f"{secrets.randbelow(10**6):06d}"
                await ws.send_json({"type": "pair_request", "device": DEVICE_ID, "code": code})
                print(f"** pairing code: {code} -> run: uv run claudechip-bridge pair {code}")

            async def send_signed(msg: dict) -> None:
                nonlocal seq
                seq += 1
                body = json.dumps(msg)
                await ws.send_json({"seq": seq, "msg": body, "sig": sign(token, f"{bridge_nonce}:{seq}:{body}")})

            async for raw in ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    print(f"<< {raw.type} {raw.data} {raw.extra}")
                    break
                data = json.loads(raw.data)
                kind = data.get("type")
                print("<<", json.dumps(data, ensure_ascii=False))
                if kind == "hello":
                    bridge_nonce = data["nonce"]
                    await (auth() if token else pair())
                elif kind == "auth_fail":
                    print("** token rejected, pairing again")
                    token = None
                    TOKEN_FILE.unlink(missing_ok=True)
                    await pair()
                elif kind == "auth_ok":
                    ok = hmac.compare_digest(data.get("mac", ""), sign(token, f"ok:{my_nonce}:{bridge_nonce}"))
                    print("** bridge verified" if ok else "** WARNING: bridge failed to prove the token")
                elif kind == "paired":
                    token = data["token"]
                    TOKEN_FILE.write_text(token)
                    print("** paired, token saved")
                    await auth()
                elif kind == "permission" and args.answer != "none":
                    if args.answer == "ask":
                        line = await asyncio.to_thread(input, f"allow {data['request_id']}? [y/n] ")
                        allow = line.strip().lower().startswith("y")
                    else:
                        allow = args.answer == "allow"
                    await send_signed({"type": "decision", "request_id": data["request_id"], "allow": allow})
                    print(">> decision", data["request_id"], allow)


if __name__ == "__main__":
    asyncio.run(main())
