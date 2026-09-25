# KBC Multiplayer — Scalable Real-Time Edition

This version upgrades the original polling-based game to a real-time multiplayer architecture:

```text
Browser players / host
        │
        │ WebSocket
        ▼
Django + Channels + Daphne
        │
        ├── Redis → WebSocket channel layer / fan-out
        ├── Celery → authoritative timer jobs
        └── PostgreSQL → durable game state
```

The game remains server-authoritative. Scores, answers, timers and lifelines are never trusted from the browser.

## What changed from the old build

- Removed the 900 ms player polling loop and 1 s host polling loop.
- Added Django Channels + WebSockets for persistent connections.
- Added Redis as the Channels layer and Celery broker/result backend.
- Added PostgreSQL configuration for production concurrency.
- Added a 1-second Celery beat job that automatically locks expired questions.
- Player heartbeat is a lightweight WebSocket ping instead of a database write every second.
- Player state is split into compact shared room state + private player state.
- Leaderboard/player-list data is kept on the host side instead of being sent to every player.
- Removed room-wide row locks from normal player answer submissions, so 150 simultaneous answers do not queue behind one `GameRoom` lock.
- Fastest Finger submissions no longer lock one shared database row for every player.
- Lifelines lock only the player's row instead of the entire room.
- Lock/reveal scoring uses bulk database operations where practical.
- Added uniqueness/indexes for high-contention tables.
- REST endpoints remain available as a fallback for environments where WebSockets are unavailable.
- Added Docker Compose for local PostgreSQL + Redis + Django/Channels + Celery worker + Celery beat.
- Added a WebSocket load-test script for 150–200 persistent clients.

## Latest hardening pass

- Host and player views no longer build markup with `innerHTML`. Every dynamic
  value (player names, question text, categories, explanations, poll results)
  is written through `textContent` / `setAttribute` with the shared helpers in
  `static/js/dom.js`, so server or player supplied text can never be parsed as
  HTML. Click handlers are attached with `addEventListener` instead of inline
  `onclick` strings.
- The commented-out legacy copies of `host.js` / `player.js` were removed, and
  `escapeHtml` is gone because escaping is no longer the defence — the DOM API
  is.
- Room codes in `/host/<code>/` and `/play/<code>/` are validated against
  `^[A-Z0-9]{6}$` before rendering, and the value is written into the page with
  Django's `escapejs` filter, so a crafted URL cannot break out of the script
  tag.
- Question deadlines are now enforced by two cooperating Celery jobs: an ETA
  task queued per question (`CELERY_DEADLINE_TASKS=True`) plus the one-second
  beat safety net. Both re-read the room, so late or duplicated deliveries are
  harmless.
- `tools/ws_load_test.py` reports join/connect latency percentiles, survives
  individual client failures, optionally simulates answers, and can run for a
  fixed duration.

## Local setup — recommended

Install Docker Desktop, then from the project root:

```bash
docker compose up --build
```

The app will be available at:

```text
http://127.0.0.1:8000/
```

The first web container automatically runs migrations, seeds starter questions and collects static files.

### Create a host account

In another terminal:

```bash
docker compose exec web python manage.py createsuperuser
```

Then open `/login/`, sign in, and open `/host/`.

## Test 200 players locally

The 200-player scenario is tested with `tools/ws_load_test.py`, which drives
exactly what a browser does:

- **Join** is `GET /join/` (sets the CSRF cookie) then `POST /api/rooms/join/`
  with `X-CSRFToken` + `Referer`. Every join sends a **unique**
  `session_id` (`str(uuid.uuid4())`), so 200 joins create 200 distinct
  players — a shared session cookie would collapse them into one player.
- Each player then opens one persistent WebSocket, authenticates, and acts on
  every state message: it submits a Fastest Finger ordering while the room
  status is `FASTEST_FINGER` and answers while a question is active.
- Late-lock errors that are normal in a 200-player run ("Answer already
  locked.", "Fastest Finger is not accepting answers.") are counted
  separately and do not fail the run.

The app uses **PostgreSQL** (no SQLite fallback). With Docker, start just
the database first:

```bash
docker compose up -d postgres   # creates the kbc database/user on port 5432
```

Then start one Daphne process and run the game (defaults match the compose
file; override with `DATABASE_URL` for anything else):

```bash
python manage.py migrate
python manage.py createsuperuser   # once
python -m daphne -b 0.0.0.0 -p 8000 config.asgi:application

python tools/ws_load_test.py --players 200 --full-game \
    --host-username admin --host-password admin \
    --ff-seconds 8 --question-seconds 6 --duration 240
```

The tool logs in as the host, creates the room, waits for all 200 players to
join, starts and finishes Fastest Finger, then runs questions 1–15 through
lock/reveal/next. A healthy run reports:

```text
Joins        : 200 ok, 0 failed
Sessions     : 200 unique player session_ids (every join sends its own uuid4 session_id)
WebSockets   : 200 connected, 0 failed, 0 auth errors
Traffic      : … events received, 3000 answers sent, 200 fastest-finger
               submissions sent, 0 server errors
```

### Players into an existing room (`--room`)

Create a room from the host UI (or the REST API), copy its six-character
code, and:

```bash
python tools/ws_load_test.py --url http://127.0.0.1:8000 --room ABC123 --players 200
```

Keep the terminal running while the host starts questions; this exercises
hundreds of persistent WebSocket connections with no HTTP polling.

Other useful options:

```text
--join-concurrency 5    parallel /api/rooms/join/ requests (default 5)
--answer-chance 1.0     probability a client answers each question
--answer-delay 2        max simulated thinking time before an answer
--ff-seconds 8          how long Fastest Finger stays open (--full-game)
--question-seconds 6    how long each question stays open (--full-game)
--duration 240          stop after N seconds (0 = run until Ctrl+C)
--json                  machine-readable summary
```

The summary reports join and WebSocket-connect latency (median and p95),
unique session count, events received, answers and Fastest Finger
submissions sent, ignored late-lock errors and failures.

### How the join/answer bursts are handled

200 players joining in seconds and answering in parallel is absorbed by the
server, not the database:

- Joins, disconnects and reconnections coalesce into at most one host
  snapshot per ~0.5 s (`broadcast_host_state_throttled`), so the lobby
  churn never rebuilds the full host state per player.
- Answers and Fastest Finger submissions send the host a small delta
  (ORM-aggregated counters only) instead of a full `state_for_host()`
  rebuild, and stage changes broadcast one public `room.state` instead of
  200 private player states.
- Answer rows use the unique `(game_question, player)` constraint instead
  of a `select_for_update` row lock, so 200 simultaneous answers do not
  serialize on one lock; a rare concurrent duplicate is recovered from the
  `IntegrityError`.

One Daphne process against one PostgreSQL is enough for a 200-player local
run. For real concurrency use the production stack: **PostgreSQL** plus a
**Redis channel layer** (set `REDIS_URL`) with multiple ASGI workers — the
in-memory channel layer only works inside a single process.

## Non-Docker local setup

You need PostgreSQL and Redis running locally.

Create `.env` from `.env.example` and set:

```text
DATABASE_URL=postgresql://kbc:kbc@127.0.0.1:5432/kbc
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=redis://127.0.0.1:6379/1
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/2
```

Then:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_questions
python manage.py createsuperuser
```

Run the ASGI server:

```bash
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

In separate terminals:

```bash
celery -A config worker -l INFO --concurrency=2
```

and:

```bash
celery -A config beat -l INFO
```

### Question deadline tasks

Celery beat locks expired questions once per second, which is enough on its
own. For tighter deadlines you can additionally queue one ETA task per question
that fires exactly at the deadline:

```text
CELERY_DEADLINE_TASKS=True
```

The web container in `docker-compose.yml` enables this by default. Leave it off
when no broker is reachable — the beat safety net keeps working either way.

## Production deployment

For a serious deployment, use:

- PostgreSQL
- Redis
- Daphne/Uvicorn/another ASGI server for WebSockets
- Multiple ASGI worker processes behind a reverse proxy/load balancer
- Celery worker(s)
- Celery beat (or another durable scheduler)
- TLS/HTTPS so browsers use `wss://`
- Monitoring and centralized logs

PythonAnywhere can still serve the Django HTTP application, but for the full WebSocket + Redis + Celery architecture you should choose a host that supports long-lived WebSocket connections and background workers cleanly. Keep PythonAnywhere as a fallback/demo deployment if desired.

## Important scaling principle

The old architecture asked every browser:

```text
"Has anything changed?" every 0.9–1.0 seconds
```

The new architecture keeps one persistent WebSocket per connected browser and the server pushes events only when game state changes.

For 150 players this changes the workload from roughly hundreds of repeated HTTP requests per minute to a small number of game-event broadcasts plus the actual player actions.

## Security notes

- No `innerHTML` anywhere in the client code: dynamic text is inserted with DOM
  APIs (`static/js/dom.js`), and event handlers are bound with
  `addEventListener`.
- Room codes are validated server-side before they are rendered or interpolated
  into a script tag.
- WebSocket authentication uses a short-lived-in-practice room/player session token sent as the first WebSocket message, not in the URL.
- Host WebSocket connections still require the authenticated Django superuser session.
- Active player state does not contain the correct answer until reveal.
- Scores are calculated server-side.
- Database constraints reject duplicate answers, fastest-finger submissions and repeated lifeline usage.
- Use HTTPS/WSS and a strong `SECRET_KEY` in production.
- Do not commit `.env` or production credentials.

## Project structure

```text
kbc_office_final/
├── config/
│   ├── asgi.py
│   ├── celery.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── game/
│   ├── consumers.py
│   ├── routing.py
│   ├── realtime.py
│   ├── tasks.py
│   ├── models.py
│   ├── services.py
│   ├── game.py
│   └── migrations/
├── static/js/
│   ├── dom.js
│   ├── host.js
│   └── player.js
├── templates/
├── tools/ws_load_test.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```
