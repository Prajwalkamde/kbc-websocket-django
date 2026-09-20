import json
import uuid
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .constants import FASTEST_PROMPT, PRIZE_LADDER, SAFE_LEVELS
from .game import state_for_host, state_for_player
from .models import AudiencePoll, ExpertRequest, GameRoom, Player
from .realtime import broadcast_host_state, broadcast_room
from .services import (
    GameError, advance_after_reveal, create_or_reconnect_player, create_room,
    expert_answer, finalize_poll, finish_fastest_finger, leaderboard,
    lock_question, maybe_timeout, pause_question, resume_question,
    reveal_question, start_fastest_finger, start_game, start_question,
    submit_answer, submit_fastest_finger,
    use_lifeline, vote_audience,
)


def json_body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise GameError("Invalid JSON body.") from exc


def error_response(exc, status=400):
    return JsonResponse({"ok": False, "error": str(exc)}, status=status)


def superuser_page_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.get_full_path()}")
        if not request.user.is_superuser:
            raise PermissionDenied("Only a superuser can host a game.")
        return view(request, *args, **kwargs)
    return wrapped


def superuser_api_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error_response(GameError("Superuser login required."), 401)
        if not request.user.is_superuser:
            return error_response(GameError("Only a superuser can host a game."), 403)
        return view(request, *args, **kwargs)
    return wrapped


def get_room(code):
    normalized_code = str(code or "").strip().upper()
    return get_object_or_404(GameRoom, room_code=normalized_code)


def host_valid(room, request):
    token = request.headers.get("X-Host-Session") or request.session.get("host_session")
    return token and str(token) == str(room.host_session)


def player_from_request(room, request):
    token = request.headers.get("X-Player-Session") or request.session.get("player_session")
    if not token:
        raise GameError("Player session missing.")
    try:
        session_id = uuid.UUID(str(token))
    except ValueError as exc:
        raise GameError("Invalid player session.")
    return Player.objects.get(room=room, session_id=session_id)


def home(request):
    return render(request, "home.html")


@superuser_page_required
def host_page(request, code=None):
    response = render(request, "host.html", {"room_code": code or ""})
    return response


def join_page(request):
    return render(request, "join.html")


def player_page(request, code):
    return render(request, "player.html", {"room_code": code.upper()})


@require_POST
@superuser_api_required
def api_create_room(request):
    try:
        room = create_room()
        request.session["host_session"] = str(room.host_session)
        request.session["host_room_code"] = room.room_code
        request.session.save()
        return JsonResponse({"ok": True, "room_code": room.room_code, "host_session": str(room.host_session)})
    except GameError as exc:
        return error_response(exc)


@require_POST
def api_join_room(request):
    try:
        body = json_body(request)
        room = get_room(body.get("room_code", ""))
        if room.status != GameRoom.Status.LOBBY:
            raise GameError("This game has already started.")
        session_token = body.get("session_id") or request.session.get("player_session") or str(uuid.uuid4())
        player = create_or_reconnect_player(room, body.get("display_name", ""), uuid.UUID(session_token))
        request.session["player_session"] = str(player.session_id)
        request.session["player_room_code"] = room.room_code
        request.session.save()
        broadcast_host_state(room)
        return JsonResponse({
            "ok": True,
            "room_code": room.room_code,
            "player_id": player.id,
            "player_session": str(player.session_id),
            "redirect": f"/play/{room.room_code}/",
        })
    except (GameRoom.DoesNotExist, Http404):
        return error_response(GameError("Invalid room code."), 404)
    except (ValueError, GameError) as exc:
        return error_response(GameError(str(exc)))


@require_GET
def api_state(request, code):
    room = get_room(code)
    changed = maybe_timeout(room)
    room.refresh_from_db()
    if request.headers.get("X-Host-Session") and host_valid(room, request):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return error_response(GameError("Only a superuser can host a game."), 403)
        return JsonResponse({"ok": True, "changed": changed, "state": state_for_host(room)})
    try:
        player = player_from_request(room, request)
    except Player.DoesNotExist:
        return error_response(GameError("Player session is not valid for this room."), 403)
    if not player.connected:
        Player.objects.filter(pk=player.pk).update(connected=True, last_seen=timezone.now())
        player.refresh_from_db()
        broadcast_room(room, event="player.connected")
    return JsonResponse({"ok": True, "changed": changed, "state": state_for_player(room, player)})


@require_POST
@superuser_api_required
def api_host_action(request, code):
    room = get_room(code)
    if not host_valid(room, request):
        return error_response(GameError("Host authorization failed."), 403)
    try:
        body = json_body(request)
        action = body.get("action")
        if action == "start_game":
            start_game(room)
        elif action == "start_fastest":
            start_fastest_finger(room)
        elif action == "finish_fastest":
            finish_fastest_finger(room)
        elif action == "start_question":
            start_question(room, int(body.get("sequence") or (room.current_question_number + 1)))
        elif action == "lock":
            lock_question(room)
        elif action == "reveal":
            reveal_question(room)
        elif action == "leaderboard":
            room.status = GameRoom.Status.LEADERBOARD
            room.save(update_fields=["status"])
        elif action == "next":
            advance_after_reveal(room)
        elif action == "pause":
            pause_question(room)
        elif action == "resume":
            resume_question(room)
        elif action == "end":
            room.status = GameRoom.Status.GAME_OVER
            room.ended_at = timezone.now()
            room.question_deadline = None
            room.paused_remaining_seconds = None
            room.save(update_fields=["status", "ended_at", "question_deadline", "paused_remaining_seconds"])
        else:
            raise GameError("Unknown host action.")
        room.refresh_from_db()
        broadcast_room(room, event="host.action")
        return JsonResponse({"ok": True})
    except (GameError, ValueError) as exc:
        return error_response(exc)


@require_POST
def api_answer(request, code):
    room = get_room(code)
    try:
        player = player_from_request(room, request)
        body = json_body(request)
        answer = submit_answer(room, player, body.get("option"))
        broadcast_room(room, event="player.answer")
        return JsonResponse({
            "ok": True,
            "locked": answer.locked,
            "selected": answer.selected_option,
            "attempts": answer.attempts,
        })
    except (Player.DoesNotExist, GameError, ValueError) as exc:
        return error_response(GameError(str(exc)))


@require_POST
def api_lifeline(request, code):
    room = get_room(code)
    try:
        player = player_from_request(room, request)
        body = json_body(request)
        key, payload = use_lifeline(room, player, body.get("lifeline"))
        broadcast_room(room, event="player.lifeline")
        return JsonResponse({"ok": True, "lifeline": key, "payload": payload})
    except (Player.DoesNotExist, GameError, ValueError) as exc:
        return error_response(GameError(str(exc)))


@require_POST
def api_fastest_submit(request, code):
    room = get_room(code)
    try:
        player = player_from_request(room, request)
        body = json_body(request)
        submission = submit_fastest_finger(room, player, body.get("answer"))
        broadcast_room(room, event="fastest.submitted")
        return JsonResponse({"ok": True, "submitted": True, "correct": submission.is_correct})
    except (Player.DoesNotExist, GameError, ValueError) as exc:
        return error_response(GameError(str(exc)))


@require_POST
def api_audience_vote(request, code):
    room = get_room(code)
    try:
        player = player_from_request(room, request)
        body = json_body(request)
        poll_id = int(body.get("poll_id"))
        poll = AudiencePoll.objects.get(id=poll_id, game_question__room=room)
        vote_audience(poll, player, body.get("option"))
        broadcast_room(room, event="audience.vote")
        return JsonResponse({"ok": True})
    except (Player.DoesNotExist, AudiencePoll.DoesNotExist, GameError, ValueError) as exc:
        return error_response(GameError(str(exc)))


@require_POST
def api_expert_answer(request, code):
    room = get_room(code)
    try:
        player = player_from_request(room, request)
        body = json_body(request)
        request_obj = ExpertRequest.objects.get(id=int(body.get("request_id")), game_question__room=room)
        option = expert_answer(request_obj, player, body.get("option"))
        broadcast_room(room, event="expert.answer")
        return JsonResponse({"ok": True, "option": option})
    except (Player.DoesNotExist, ExpertRequest.DoesNotExist, GameError, ValueError) as exc:
        return error_response(GameError(str(exc)))


@require_GET
def api_poll(request, code, poll_id):
    room = get_room(code)
    from .models import AudiencePoll
    try:
        player = player_from_request(room, request)
        poll = AudiencePoll.objects.get(id=poll_id, game_question__room=room)
        counts, percentages = finalize_poll(poll)
        return JsonResponse({
            "ok": True,
            "completed": poll.completed,
            "counts": counts,
            "percentages": percentages,
            "votes": sum(counts.values()),
        })
    except (Player.DoesNotExist, AudiencePoll.DoesNotExist, GameError, ValueError) as exc:
        return error_response(GameError(str(exc)))


def admin_seed_needed():
    return None
