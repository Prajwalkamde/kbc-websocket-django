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

## Test 150–200 players locally

Install dev dependencies on your machine:

```bash
pip install -r requirements-dev.txt
```

Create a room from the host UI and copy its six-character code. Then run:

```bash
python tools/ws_load_test.py --url http://127.0.0.1:8000 --room ABC123 --players 150
```

For 200 players:

```bash
python tools/ws_load_test.py --url http://127.0.0.1:8000 --room ABC123 --players 200
```

Keep the terminal running while the host starts questions. This tests the important part of the new architecture: hundreds of persistent WebSocket connections without hundreds of HTTP polling requests per second.

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
