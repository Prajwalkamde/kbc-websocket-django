"""Open many KBC WebSocket clients to a pre-created room.

Usage:
  python tools/ws_load_test.py --url ws://127.0.0.1:8000 --room ABC123 --players 150

The script sends the session token only after the socket is established, so it
is not placed in the URL. It is intended for local/staging load tests.
"""
import argparse
import asyncio
import json
import time
import uuid

import httpx
import websockets


async def join(http, base, room, name):
    r = await http.post(f"{base}/api/rooms/join/", json={"room_code": room, "display_name": name})
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data["player_session"]


async def client(ws_base, room, token, ready):
    uri = f"{ws_base}/ws/rooms/{room}/"
    async with websockets.connect(uri, ping_interval=20, ping_timeout=20, max_size=2**20) as ws:
        await ws.send(json.dumps({"type": "auth", "role": "player", "token": token}))
        first = json.loads(await ws.recv())
        # The consumer sends hello first, then authenticated.
        if first.get("type") == "hello":
            first = json.loads(await ws.recv())
        if first.get("type") != "authenticated":
            raise RuntimeError(first)
        ready.set()
        while True:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=30)
                if message is None:
                    return
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"type": "ping"}))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--room", required=True)
    ap.add_argument("--players", type=int, default=150)
    args = ap.parse_args()
    base = args.url.rstrip("/")
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")

    async with httpx.AsyncClient(timeout=10) as http:
        tokens = await asyncio.gather(*[
            join(http, base, args.room.upper(), f"LoadUser-{i+1}")
            for i in range(args.players)
        ])

    ready_events = [asyncio.Event() for _ in tokens]
    started = time.perf_counter()
    tasks = [asyncio.create_task(client(ws_base, args.room.upper(), token, ready)) for token, ready in zip(tokens, ready_events)]
    await asyncio.gather(*(event.wait() for event in ready_events))
    elapsed = time.perf_counter() - started
    print(f"Connected {len(tasks)} players in {elapsed:.2f}s")
    print("Players are now holding persistent WebSocket connections. Press Ctrl+C to stop.")
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
