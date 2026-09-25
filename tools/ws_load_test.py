"""Open many KBC WebSocket clients against a room and drive the whole game.

This tool exercises exactly what a browser does:

* **Join** is ``GET /join/`` (which sets the CSRF cookie) followed by
  ``POST /api/rooms/join/`` with ``X-CSRFToken`` + ``Referer``. Every join
  sends a **unique** ``session_id`` (``str(uuid.uuid4())``), so 200 joins
  create 200 distinct players instead of a handful of players sharing one
  cookie/session.
* **Fastest Finger**: when the room state says ``FASTEST_FINGER``, each
  client submits one ordering (not only question answers).
* **Late locks are expected**: errors like "Fastest Finger is not accepting
  answers." or "Answer already locked." that arrive a few milliseconds after
  the host locked the round are counted separately and do not fail the run.

Examples
--------
200 players into a room the host UI is running::

    python tools/ws_load_test.py --url http://127.0.0.1:8000 --room ABC123 --players 200

Fully self-contained 200-player game (logs in as the host, creates the room,
starts Fastest Finger, finishes it, then starts questions 1..15)::

    python tools/ws_load_test.py --players 200 --full-game \
        --host-username admin --host-password admin

Useful options:

--join-concurrency 5     parallel join HTTP bursts (default 5)
--answer-chance 1.0      probability a client answers each question
--answer-delay 2         max simulated thinking time before an answer
--ff-seconds 12          how long Fastest Finger stays open (--full-game)
--question-seconds 12    how long each question stays open (--full-game)
--duration 0             stop after N seconds (0 = until Ctrl+C)
--json                   machine-readable summary
"""
import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx
import websockets

ANSWER_OPTIONS = ("A", "B", "C", "D")
# Only a truly active question accepts answers; submitting anything else just
# produces (ignored) late-lock errors.
ACTIVE_STATUSES = {"QUESTION_ACTIVE"}
FASTEST_FINGER_STATUS = "FASTEST_FINGER"

# Late arrivals after the host locked the round are normal in a 200-player
# run and must not count as failures.
IGNORED_ERROR_PHRASES = (
    "fastest finger is not accepting answers",
    "answer already locked",
    "you already submitted",
    "question is not accepting answers",
    "time is up",
    "you already used that option",
    "lifeline is unavailable or already used",
    "lifeline already used for this question",
)

CSRF_TOKEN_RE = re.compile(r"window\.CSRF_TOKEN=\"([^\"]+)\"")


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
        self.ff_submissions = 0
        self.ignored_errors = 0
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
            "ff_submissions": self.ff_submissions,
            "ignored_errors": self.ignored_errors,
            "errors": self.errors,
        }


def report(stats, players, elapsed, unique_sessions, as_json=False):
    data = stats.snapshot()
    data["players_requested"] = players
    data["unique_sessions"] = unique_sessions
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
        f"Sessions     : {data['unique_sessions']} unique player session_ids "
        f"(every join sends its own uuid4 session_id)"
    )
    print(
        f"WebSockets   : {data['socket_ok']} connected, {data['socket_failed']} failed, "
        f"{data['auth_failed']} auth errors "
        f"(median {data['socket_median_seconds']}s, p95 {data['socket_p95_seconds']}s)"
    )
    print(
        f"Traffic      : {data['events']} events received, "
        f"{data['answers']} answers sent, {data['ff_submissions']} fastest-finger "
        f"submissions sent, {data['ignored_errors']} ignored late-lock errors, "
        f"{data['errors']} server errors"
    )
    print(f"Elapsed      : {data['elapsed_seconds']}s for {players} players")
    print("====================================================")


def csrf_token_from(client, page_text, path):
    """Prefer the csrftoken cookie set by the page, fall back to the token
    embedded as window.CSRF_TOKEN in the page markup."""
    token = client.cookies.get("csrftoken")
    if token:
        return token
    match = CSRF_TOKEN_RE.search(page_text)
    if not match:
        raise RuntimeError(f"no CSRF token available from {path}")
    return match.group(1)


async def join_player(client, base, room, name, session_id):
    """Browser-faithful join for one player.

    ``GET /join/`` warms the CSRF cookie, then ``POST /api/rooms/join/`` runs
    with ``X-CSRFToken`` + ``Referer``. ``session_id`` is always unique per
    simulated player, which is what makes 200 joins create 200 players.
    """
    page = await client.get(f"{base}/join/")
    page.raise_for_status()
    token = csrf_token_from(client, page.text, "/join/")

    response = await client.post(
        f"{base}/api/rooms/join/",
        json={
            "room_code": room,
            "display_name": name,
            "session_id": session_id,
        },
        headers={
            "X-CSRFToken": token,
            "Referer": f"{base}/join/",
            "Content-Type": "application/json",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or payload)
    return payload["player_session"]


async def join_and_play(ws_base, room, args, stats, quiet, stop_at, tokens, session_ids, ready_events):
    """Join every simulated player and open their WebSocket immediately.

    Each worker gets its own ``httpx.AsyncClient`` (its own cookie jar) so no
    two simulated players can share a session cookie. As soon as one player's
    join succeeds the worker opens that player's socket — exactly what a
    browser does on the join redirect — so the socket fan-out overlaps the
    join fan-out instead of waiting for all 200 joins to finish first.
    """
    base = args.url.rstrip("/")
    semaphore = asyncio.Semaphore(args.join_concurrency)

    async def worker(index):
        ready = ready_events[index]
        started = time.perf_counter()
        # The semaphore bounds the JOIN burst only. A real browser tab keeps
        # its socket open for the whole game, so the socket phase must not
        # hold a join slot — otherwise 200 players "join" one slot at a time
        # over the entire run instead of bursting.
        async with semaphore:
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    tokens[index] = await join_player(
                        client, base, room, f"LoadUser-{index + 1}", session_ids[index]
                    )
            except Exception as exc:  # noqa: BLE001 - the report needs every failure
                stats.join_failed += 1
                ready.set()
                if not quiet:
                    print(f"  join failed for LoadUser-{index + 1}: {exc}")
                return
            stats.join_ok += 1
            stats.join_seconds.append(time.perf_counter() - started)

        token = tokens[index]
        socket_started = time.perf_counter()
        try:
            await player_socket(
                ws_base, room, token, args, stats, ready, stop_at, quiet, socket_started
            )
        except Exception as exc:  # noqa: BLE001 - one bad client must not kill the run
            stats.socket_failed += 1
            ready.set()
            if not quiet:
                print(f"  socket error: {exc}")

    await asyncio.gather(*(worker(i) for i in range(len(session_ids))))


async def maybe_submit_fastest(socket, state, stats, submitted_prompts):
    """Submit one fastest-finger ordering per live round, like a player would."""
    if state.get("status") != FASTEST_FINGER_STATUS:
        return
    ff = state.get("fastest") or {}
    if not ff.get("started") or ff.get("locked"):
        return
    prompt = ff.get("prompt") or {}
    options = prompt.get("options") or []
    if not options:
        return
    # One submission per distinct prompt (a host may restart the round with a
    # fresh prompt before Question 1).
    key = prompt.get("text") or json.dumps(options, sort_keys=True)
    if key in submitted_prompts:
        return
    submitted_prompts.add(key)
    await socket.send(
        json.dumps(
            {
                "type": "action",
                "action": "fastest_submit",
                "answer": random.choice(options),
            }
        )
    )
    stats.ff_submissions += 1


async def maybe_answer(socket, state, args, stats, answered, question_deadline):
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


async def player_socket(ws_base, room, token, args, stats, ready, stop_at, quiet, started):
    """Hold one persistent socket, answering questions and submitting FF."""
    uri = f"{ws_base}/ws/rooms/{room}/"
    answered = set()
    ff_submitted_prompts = set()
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
                # The server includes the current room state in the
                # authenticated message. A browser that (re)connects mid-game
                # must act on it too — otherwise a player joining during a
                # live Fastest Finger round never submits.
                state = message.get("state") or {}
                if state:
                    if message.get("role") == "player":
                        question_deadline = state.get("deadline")
                    await maybe_submit_fastest(socket, state, stats, ff_submitted_prompts)
                    await maybe_answer(socket, state, args, stats, answered, question_deadline)
                continue
            if message_type == "auth_error":
                stats.auth_failed += 1
                ready.set()
                return
            if message_type == "error":
                error = str(message.get("error") or "").strip().lower()
                if any(phrase in error for phrase in IGNORED_ERROR_PHRASES):
                    stats.ignored_errors += 1
                else:
                    stats.errors += 1
                continue

            stats.events += 1

            if message_type in {"room.state", "player.private"}:
                state = message.get("state") or {}
                if message_type == "room.state":
                    question_deadline = state.get("deadline")
                await maybe_submit_fastest(socket, state, stats, ff_submitted_prompts)
                await maybe_answer(
                    socket, state, args, stats, answered, question_deadline
                )

            if stop_at and time.monotonic() >= stop_at:
                ready.set()
                return


class HostDriver:
    """Log in as the host, create the room, and drive the game over REST.

    This lets ``--full-game`` run the entire 200-player scenario without a
    human at the host UI: start Fastest Finger, finish it, then run questions
    1..15 through lock/reveal/next.
    """

    def __init__(self, base, username, password, args, stop_at, quiet):
        self.base = base
        self.username = username
        self.password = password
        self.args = args
        self.stop_at = stop_at
        self.quiet = quiet
        self.room_code = None
        self.host_session = None
        self.http = httpx.AsyncClient(timeout=20, follow_redirects=False)

    async def _csrf(self, path):
        page = await self.http.get(f"{self.base}{path}")
        page.raise_for_status()
        return csrf_token_from(self.http, page.text, path)

    async def login(self):
        token = await self._csrf("/login/")
        response = await self.http.post(
            f"{self.base}/login/",
            data={
                "username": self.username,
                "password": self.password,
                "next": "/host/",
            },
            headers={
                "X-CSRFToken": token,
                "Referer": f"{self.base}/login/",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if response.status_code not in (301, 302, 303):
            raise RuntimeError(f"host login failed: HTTP {response.status_code}")

    async def create_room(self):
        token = await self._csrf("/host/")
        response = await self.http.post(
            f"{self.base}/api/rooms/create/",
            json={},
            headers={
                "X-CSRFToken": token,
                "Referer": f"{self.base}/host/",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(payload.get("error") or payload)
        self.room_code = payload["room_code"]
        self.host_session = payload["host_session"]

    async def action(self, action, **body):
        # The host-action endpoint is CSRF-protected like every browser POST.
        token = self.http.cookies.get("csrftoken")
        if not token:
            token = await self._csrf(f"/host/{self.room_code}/")
        response = await self.http.post(
            f"{self.base}/api/rooms/{self.room_code}/host-action/",
            json={"action": action, **body},
            headers={
                "X-Host-Session": self.host_session,
                "X-CSRFToken": token,
                "Referer": f"{self.base}/host/{self.room_code}/",
                "Content-Type": "application/json",
            },
        )
        if response.status_code != 200:
            try:
                detail = response.json().get("error")
            except ValueError:
                detail = f"HTTP {response.status_code}"
            raise RuntimeError(f"host action {action} failed: {detail}")
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"host action {action} failed: {payload.get('error')}")

    async def player_count(self):
        response = await self.http.get(
            f"{self.base}/api/rooms/{self.room_code}/state/",
            headers={"X-Host-Session": self.host_session},
        )
        if response.status_code != 200:
            return 0
        state = response.json().get("state") or {}
        return int(state.get("player_count") or 0)

    async def wait_for_players(self, target, timeout=120):
        deadline = time.monotonic() + timeout
        joined = 0
        while time.monotonic() < deadline:
            joined = await self.player_count()
            if joined >= target:
                break
            await asyncio.sleep(1)
        return joined

    async def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop_at and time.monotonic() >= self.stop_at:
                return
            await asyncio.sleep(0.25)

    def stopped(self):
        return bool(self.stop_at) and time.monotonic() >= self.stop_at

    async def run(self):
        await self.login()
        await self.create_room()
        if not self.quiet:
            print(f"Host driver: room {self.room_code} created.")

        # Wait for the whole simulated cohort (up to the join timeout) so that
        # all 200 players are in the lobby before Fastest Finger starts; a
        # host starts the game once everyone is seated, not after 50.
        target = max(1, self.args.players)
        joined = await self.wait_for_players(target, timeout=180)
        if not self.quiet:
            print(f"Host driver: {joined} players joined, starting Fastest Finger.")

        await self.action("start_fastest")
        await self._sleep(self.args.ff_seconds)
        if self.stopped():
            return
        await self.action("finish_fastest")
        if not self.quiet:
            print("Host driver: Fastest Finger finished, starting Question 1.")

        for sequence in range(1, 16):
            if self.stopped():
                return
            await self.action("start_question", sequence=sequence)
            await self._sleep(self.args.question_seconds)
            if self.stopped():
                return
            await self.action("lock")
            await self.action("reveal")
            await self._sleep(3)
            if self.stopped():
                return
            await self.action("next")

        if not self.quiet:
            print("Host driver: all questions finished.")

    async def close(self):
        await self.http.aclose()


async def wait_for_room_code(driver, task, timeout=30):
    """Block until the host driver has logged in and created the room."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if driver.room_code:
            return driver.room_code
        if task.done():
            raise RuntimeError("host driver exited before creating a room")
        await asyncio.sleep(0.2)
    raise RuntimeError("timed out waiting for the host driver to create a room")


async def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="HTTP base URL of the app")
    parser.add_argument("--room", help="six-character room code created by the host")
    parser.add_argument("--full-game", action="store_true",
                        help="log in as host, create the room, and drive FF + questions")
    parser.add_argument("--host-username", default="admin", help="host superuser username (--full-game)")
    parser.add_argument("--host-password", default="admin", help="host superuser password (--full-game)")
    parser.add_argument("--players", type=int, default=200, help="number of clients to connect")
    parser.add_argument("--join-concurrency", type=int, default=5,
                        help="parallel /api/rooms/join/ requests (default 5)")
    parser.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = run until Ctrl+C)")
    parser.add_argument("--answer-chance", type=float, default=1.0,
                        help="probability a client answers each question (0-1)")
    parser.add_argument("--answer-delay", type=float, default=2.0,
                        help="max simulated thinking time before an answer")
    parser.add_argument("--ff-seconds", type=float, default=12.0,
                        help="how long Fastest Finger stays open (--full-game)")
    parser.add_argument("--question-seconds", type=float, default=12.0,
                        help="how long each question stays open (--full-game)")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args(argv)

    if not args.room and not args.full_game:
        parser.error("either --room CODE or --full-game is required")
    if args.room and args.full_game:
        parser.error("--room and --full-game are mutually exclusive")
    if not 0 <= args.answer_chance <= 1:
        parser.error("--answer-chance must be between 0 and 1")
    if args.players < 1:
        parser.error("--players must be at least 1")

    base = args.url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    stats = Stats()
    stop_at = time.monotonic() + args.duration if args.duration else None
    started = time.perf_counter()

    room = args.room.strip().upper() if args.room else None
    driver = None
    driver_task = None
    if args.full_game:
        driver = HostDriver(base, args.host_username, args.host_password, args, stop_at, args.quiet)
        driver_task = asyncio.create_task(driver.run())
        room = await wait_for_room_code(driver, driver_task)

    if not args.quiet:
        print(f"Joining {args.players} players to room {room} …")

    tokens = [None] * args.players
    session_ids = [str(uuid.uuid4()) for _ in range(args.players)]
    ready_events = [asyncio.Event() for _ in range(args.players)]
    play_task = asyncio.create_task(
        join_and_play(ws_base, room, args, stats, args.quiet, stop_at, tokens, session_ids, ready_events)
    )

    try:
        await asyncio.gather(*(event.wait() for event in ready_events))
    except asyncio.CancelledError:
        pass

    unique_sessions = len({token for token in tokens if token})
    if not args.quiet:
        print(f"Unique player sessions: {unique_sessions} of {args.players} requested")

    if not any(tokens):
        play_task.cancel()
        if driver_task:
            driver_task.cancel()
        report(stats, args.players, time.perf_counter() - started, unique_sessions, args.json)
        return 1

    if not args.quiet:
        print(
            f"{stats.socket_ok} of {args.players} sockets authenticated "
            f"({unique_sessions} unique sessions). Press Ctrl+C to stop."
        )

    try:
        await play_task
    except asyncio.CancelledError:
        pass
    finally:
        play_task.cancel()
        if driver_task:
            driver_error = None
            if not driver_task.done():
                driver_task.cancel()
            try:
                await driver_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - surface driver failures in the summary
                driver_error = exc
            if driver_error and not args.quiet:
                print(f"Host driver error: {driver_error}")
        if driver:
            await driver.close()

    report(stats, args.players, time.perf_counter() - started, unique_sessions, args.json)
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
