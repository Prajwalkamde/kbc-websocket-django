import threading

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models import Count, Q

from .game import public_room_state, state_for_host, state_for_player
from .models import Answer, FastestFingerRound, GameRoom, Player

# Joins, disconnects and other high-frequency player events coalesce into at
# most one full host snapshot per room per this interval. With 200 players a
# lobby churn burst must not mean 200 state_for_host() rebuilds + fan-outs.
HOST_STATE_THROTTLE_SECONDS = 0.5

_throttle_lock = threading.Lock()
_pending_host_flushes = {}


def room_group(room_code):
    return f"room_{str(room_code).upper()}"


def host_group(room_code):
    return f"host_{str(room_code).upper()}"


def player_group(player_id):
    return f"player_{player_id}"


def _send(group, event_name, payload):
    layer = get_channel_layer()
    if not layer:
        return
    async_to_sync(layer.group_send)(group, {"type": event_name, "payload": payload})


def broadcast_room(room, *, event="room.state"):
    """Push one compact room event plus the host's view.

    Player actions avoid rebuilding the expensive fastest-finger/final-results
    view unless the event actually needs it.
    """
    payload = public_room_state(room)
    payload["event"] = event
    _send(room_group(room.room_code), "room.state", payload)
    _cancel_throttled_host_state(room)
    host_state = state_for_host(room)
    _send(host_group(room.room_code), "host_state_event", host_state)


def send_player_state(player_id, room):
    player = Player.objects.select_related("room").get(pk=player_id)
    _send(player_group(player_id), "player_state_event", state_for_player(room, player))


def broadcast_private_player_states(room):
    """Push each player's private view so question-local answer state cannot leak."""
    for player in room.players.all():
        send_player_state(player.id, room)


def broadcast_host_state(room):
    """Immediate full host snapshot.

    A fresh snapshot supersedes any coalesced update still in flight, so the
    pending throttle for this room is cancelled first: a stale snapshot must
    never arrive after the one this call just sent.
    """
    _cancel_throttled_host_state(room)
    _send(host_group(room.room_code), "host_state_event", state_for_host(room))


def broadcast_host_state_throttled(room, *, interval=HOST_STATE_THROTTLE_SECONDS):
    """Coalesce bursts of joins/disconnects into one host snapshot per interval.

    The snapshot is rebuilt at flush time (not captured now), so however many
    players join in the window the host receives a single, current state.
    """
    code = str(room.room_code).upper()
    with _throttle_lock:
        if code in _pending_host_flushes:
            return
        timer = threading.Timer(interval, _flush_throttled_host_state, args=(code,))
        timer.daemon = True
        _pending_host_flushes[code] = timer
        timer.start()


def _cancel_throttled_host_state(room):
    code = str(room.room_code).upper()
    with _throttle_lock:
        timer = _pending_host_flushes.pop(code, None)
    if timer is not None:
        timer.cancel()


def _flush_throttled_host_state(code):
    with _throttle_lock:
        _pending_host_flushes.pop(code, None)
    try:
        room = GameRoom.objects.filter(room_code=code).first()
        if room is None:
            return
        _send(host_group(code), "host_state_event", state_for_host(room))
    except Exception:
        # The room (or database) vanished between scheduling and flushing;
        # a coalesced update is best-effort by design.
        pass
    finally:
        # This runs on a plain threading.Timer thread, not a request thread:
        # without this the thread's database connection would sit idle (and on
        # PostgreSQL block DROP DATABASE when the test runner tears down).
        from django.db import connections

        connections.close_all()


def broadcast_players(room, *, event="players.updated"):
    payload = public_room_state(room)
    payload["event"] = event
    _send(room_group(room.room_code), "room.state", payload)
    _cancel_throttled_host_state(room)
    _send(host_group(room.room_code), "host_state_event", state_for_host(room))


def broadcast_host_delta(room, *, event="delta"):
    """Lightweight host counters for high-frequency player actions.

    The host UI updates only the answer counters / Fastest Finger submission
    number from this payload; nothing here rebuilds the full host state.
    Counts are aggregated in the database so 200 answers per question do not
    pull 200 answer rows into Python.
    """
    from .game import _current_question

    payload = {"event": event}
    if event in {"player.answer", "player.lock"}:
        gq = _current_question(room)
        if gq:
            totals = Answer.objects.filter(game_question=gq).aggregate(
                submitted=Count("id", filter=Q(selected_option__isnull=False)),
                locked=Count("id", filter=Q(locked=True)),
            )
            distribution = {letter: 0 for letter in "ABCD"}
            for row in Answer.objects.filter(
                game_question=gq, selected_option__isnull=False
            ).values("selected_option").annotate(count=Count("id")):
                if row["selected_option"] in distribution:
                    distribution[row["selected_option"]] = row["count"]
            payload["answers"] = {
                "submitted": totals["submitted"] or 0,
                "locked": totals["locked"] or 0,
                "distribution": distribution,
            }
    elif event == "fastest.submitted":
        ff = FastestFingerRound.objects.filter(room=room).first()
        payload["fastest"] = {"submissions": ff.submissions.count() if ff else 0}
    _send(host_group(room.room_code), "host_delta_event", payload)
