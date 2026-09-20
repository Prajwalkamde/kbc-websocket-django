from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .game import public_room_state, state_for_host, state_for_player
from .models import GameRoom, Player


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
    host_state = state_for_host(room)
    _send(host_group(room.room_code), "host_state_event", host_state)


def send_player_state(player_id, room):
    player = Player.objects.select_related("room").get(pk=player_id)
    _send(player_group(player_id), "player_state_event", state_for_player(room, player))


def broadcast_host_state(room):
    _send(host_group(room.room_code), "host_state_event", state_for_host(room))


def broadcast_players(room, *, event="players.updated"):
    payload = public_room_state(room)
    payload["event"] = event
    _send(room_group(room.room_code), "room.state", payload)
    _send(host_group(room.room_code), "host_state_event", state_for_host(room))


def broadcast_host_delta(room, *, event="delta"):
    from .game import _current_question
    from .models import Answer, FastestFingerRound
    payload = {"event": event}
    if event in {"player.answer", "player.lock"}:
        gq = _current_question(room)
        answers = list(Answer.objects.filter(game_question=gq)) if gq else []
        payload["answers"] = {
            "submitted": sum(bool(a.attempts) for a in answers),
            "locked": sum(bool(a.locked) for a in answers),
            "distribution": {letter: sum(a.selected_option == letter for a in answers) for letter in "ABCD"},
        }
    elif event == "fastest.submitted":
        ff = FastestFingerRound.objects.filter(room=room).first()
        payload["fastest"] = {"submissions": ff.submissions.count() if ff else 0}
    _send(host_group(room.room_code), "host_delta_event", payload)
