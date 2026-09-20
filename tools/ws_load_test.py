"""Open many KBC WebSocket clients against a pre-created room.

Examples
--------
150 players, idle connections (the original smoke test)::

    python tools/ws_load_test.py --url http://127.0.0.1:8000 --room ABC123 --players 150

200 players that also answer questions for 90 seconds::

    python tools/ws_load_test.py --room ABC123 --players 200 --answer-chance 0.9 \
        --duration 90

The session token is sent in the first WebSocket message, never in the URL, so
the tool exercises exactly the same handshake a browser does. It is intended
for local/staging load tests: start the host UI, begin Fastest Finger and keep
starting questions while this script runs.
"""
import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone

import httpx
import websockets

ANSWER_OPTIONS = ("A", "B", "C", "D")
ACTIVE_STATUSES = {"QUESTION_ACTIVE", "PAUSED", "ANSWER_LOCKED", "REVEAL"}


def percentile(values, fraction):
    """Return a simple nearest-rank percentile for a list of numbers."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))
    return ordered[index]


class Stats:
    """Shared counters used to print the final report."""

    def __init__(self):
        self.join_ok = 0
        self.join_failed = 0
        self.join_seconds = []
        self.socket_ok = 0
        self.socket_failed = 0
        self.socket_seconds = []
        self.auth_failed = 0
        self.events = 0
        self.answers = 0
        self.lifelines = 0
        self.errors = 0

    def snapshot(self):
        return {
            "join_ok": self.join_ok,
            "join_failed": self.join_failed,
            "join_median_seconds": round(statistics.median(self.join_seconds), 3) if self.join_seconds else 0.0,
            "join_p95_seconds": round(percentile(self.join_seconds, 0.95), 3),
            "socket_ok": self.socket_ok,
            "socket_failed": self.socket_failed,
            "socket_median_seconds": round(statistics.median(self.socket_seconds), 3) if self.socket_seconds else 0.0,
            "socket_p95_seconds": round(percentile(self.socket_seconds, 0.95), 3),
            "auth_failed": self.auth_failed,
            "events": self.events,
            "answers": self.answers,
            "lifelines": self.lifelines,
            "errors": self.errors,
        }


def report(stats, players, elapsed, as_json=False):
    data = stats.snapshot()
    data["players_requested"] = players
    data["elapsed_seconds"] = round(elapsed, 2)

    if as_json:
        print(json.dumps(data, indent=2))
        return

    print("")
    print("================ load test summary ================")
    print(
        f"Joins        : {data['join_ok']} ok, {data['join_failed']} failed "
        f"(median {data['join_median_seconds']}s, p95 {data['join_p95_seconds']}s)"
    )
    print(
        f"WebSockets   : {data['socket_ok']} connected, {data['socket_failed']} failed, "
        f"{data['auth_failed']} auth errors "
        f"(median {data['socket_median_seconds']}s, p95 {data['socket_p95_seconds']}s)"
    )
    print(
        f"Traffic      : {data['events']} events received, "
        f"{data['answers']} answers sent, {data['errors']} server errors"
    )
    print(f"Elapsed      : {data['elapsed_seconds']}s for {players} players")
    print("===================================================")


async def join_player(http, base, room, name):
    response = await http.post(
        f"{base}/api/rooms/join/",
        json={"room_code": room, "display_name": name},
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or payload)
    return payload["player_session"]


async def join_all(base, room, players, concurrency, stats, quiet):
    """Join ``players`` sessions, keeping the HTTP fan-out bounded."""
    semaphore = asyncio.Semaphore(concurrency)
    tokens = [None] * players

    async with httpx.AsyncClient(timeout=15) as http:

        async def worker(index):
            async with semaphore:
                started = time.perf_counter()
                try:
                    tokens[index] = await join_player(
                        http, base, room, f"LoadUser-{index + 1}"
                    )
                except Exception as exc:  # noqa: BLE001 - the report needs every failure
                    stats.join_failed += 1
                    if not quiet:
                        print(f"  join failed for LoadUser-{index + 1}: {exc}")
                    return
                stats.join_ok += 1
                stats.join_seconds.append(time.perf_counter() - started)

        await asyncio.gather(*(worker(i) for i in range(players)))

    return [token for token in tokens if token]


async def player_socket(ws_base, room, token, args, stats, ready, stop_at, quiet, started):
    """Hold one persistent socket, optionally answering questions."""
    uri = f"{ws_base}/ws/rooms/{room}/"
    answered = set()
    question_deadline = None

    async with websockets.connect(
        uri,
        ping_interval=20,
        ping_timeout=20,
        max_size=2 ** 20,
        open_timeout=15,
    ) as socket:
        await socket.send(json.dumps({"type": "auth", "role": "player", "token": token}))

        while True:
            timeout = 5
            if stop_at:
                timeout = max(0.5, min(timeout, stop_at - time.monotonic()))
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                if stop_at and time.monotonic() >= stop_at:
                    ready.set()
                    return
                await socket.send(json.dumps({"type": "ping"}))
                continue

            message = json.loads(raw)
            message_type = message.get("type")

            if message_type == "hello":
                continue
            if message_type == "authenticated":
                stats.socket_ok += 1
                stats.socket_seconds.append(time.perf_counter() - started)
                ready.set()
                continue
            if message_type in {"auth_error"}:
                stats.auth_failed += 1
                ready.set()
                return
            if message_type == "error":
                stats.errors += 1
                continue

            stats.events += 1

            if message_type in {"room.state", "player.private"}:
                state = message.get("state") or {}
                if message_type == "room.state":
                    question_deadline = state.get("deadline")
                await maybe_answer(
                    socket, state, args, stats, answered, question_deadline, quiet
                )

            if stop_at and time.monotonic() >= stop_at:
                ready.set()
                return


async def maybe_answer(socket, state, args, stats, answered, question_deadline, quiet):
    """Submit one simulated answer per question when the room is live."""
    if args.answer_chance <= 0:
        return
    if state.get("status") not in ACTIVE_STATUSES:
        return

    number = state.get("current_question")
    if not number or number in answered:
        return

    answered.add(number)

    if question_deadline:
        try:
            remaining = (
                datetime.fromisoformat(question_deadline.replace("Z", "+00:00"))
                - datetime.now(timezone.utc)
            ).total_seconds()
            if remaining <= 0:
                return
            await asyncio.sleep(random.uniform(0, min(args.answer_delay, remaining)))
        except ValueError:
            pass
    else:
        await asyncio.sleep(random.uniform(0, args.answer_delay))

    if random.random() > args.answer_chance:
        return

    await socket.send(
        json.dumps(
            {
                "type": "action",
                "action": "answer",
                "option": random.choice(ANSWER_OPTIONS),
            }
        )
    )
    stats.answers += 1


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="HTTP base URL of the app")
    parser.add_argument("--room", required=True, help="six-character room code created by the host")
    parser.add_argument("--players", type=int, default=150, help="number of clients to connect")
    parser.add_argument("--join-concurrency", type=int, default=25, help="parallel /api/rooms/join/ requests")
    parser.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = run until Ctrl+C)")
    parser.add_argument("--answer-chance", type=float, default=0, help="probability a client answers each question (0-1)")
    parser.add_argument("--answer-delay", type=float, default=2.0, help="max simulated thinking time before an answer")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args(argv)

    if not 0 <= args.answer_chance <= 1:
        parser.error("--answer-chance must be between 0 and 1")
    if args.players < 1:
        parser.error("--players must be at least 1")

    room = args.room.strip().upper()
    base = args.url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    stats = Stats()

    if not args.quiet:
        print(f"Joining {args.players} players to room {room} …")

    started = time.perf_counter()
    tokens = await join_all(base, room, args.players, args.join_concurrency, stats, args.quiet)

    if not tokens:
        report(stats, args.players, time.perf_counter() - started, args.json)
        return 1

    ready_events = [asyncio.Event() for _ in tokens]
    stop_at = time.monotonic() + args.duration if args.duration else None

    async def guarded(token, ready):
        begin = time.perf_counter()
        try:
            await player_socket(
                ws_base, room, token, args, stats, ready, stop_at, args.quiet, begin
            )
        except Exception as exc:  # noqa: BLE001 - one bad client must not kill the run
            stats.socket_failed += 1
            ready.set()
            if not args.quiet:
                print(f"  socket error: {exc}")

    tasks = [
        asyncio.create_task(guarded(token, ready))
        for token, ready in zip(tokens, ready_events)
    ]

    try:
        await asyncio.gather(*(event.wait() for event in ready_events))
    except asyncio.CancelledError:
        pass

    if not args.quiet:
        print(f"{stats.socket_ok} of {len(tasks)} sockets authenticated. Press Ctrl+C to stop.")

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for task in tasks:
            task.cancel()

    report(stats, args.players, time.perf_counter() - started, args.json)
    return 1 if (stats.join_failed or stats.socket_failed or stats.auth_failed) else 0


def run():
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        exit_code = 0
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
