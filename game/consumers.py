import uuid

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .game import state_for_host, state_for_player
from .models import AudiencePoll, ExpertRequest, GameRoom, Player
from .realtime import (
    broadcast_host_delta,
    broadcast_host_state_throttled,
    broadcast_room,
    send_player_state,
    player_group,
)
from .services import (
    GameError,
    advance_after_reveal,
    expert_answer,
    finish_fastest_finger,
    lock_question,
    pause_question,
    reveal_question,
    resume_question,
    start_fastest_finger,
    start_game,
    start_question,
    submit_answer,
    submit_fastest_finger,
    use_lifeline,
    vote_audience,
)


class GameConsumer(AsyncJsonWebsocketConsumer):
    """One persistent socket per host/player. REST remains available as a fallback."""

    async def connect(self):
        self.code = self.scope["url_route"]["kwargs"]["code"].upper()
        self.token = None
        self.role = None
        self.player = None
        self.room = await self.get_room(self.code)
        if not self.room:
            await self.close(code=4404)
            return
        await self.accept()
        await self.send_json({"type": "hello", "message": "connected"})

    async def receive_json(self, content, **kwargs):
        try:
            message_type = content.get("type")
            if message_type == "auth":
                await self.authenticate(str(content.get("token") or ""), str(content.get("role") or "player"))
                return
            if not self.role:
                raise GameError("Authenticate the WebSocket first.")
            if message_type == "ping":
                if self.role == "player" and self.player:
                    await self.touch_player(self.player.id)
                await self.send_json({"type": "pong"})
                return
            if self.role == "host":
                await self.handle_host_action(content)
            else:
                await self.handle_player_action(content)
        except GameError as exc:
            await self.send_json({"type": "error", "error": str(exc)})
        except (ValueError, TypeError) as exc:
            await self.send_json({"type": "error", "error": str(exc)})

    async def authenticate(self, token, role):
        if self.role:
            return
        if role == "host":
            if not await self.is_valid_host(token):
                await self.send_json({"type": "auth_error", "error": "Host authorization failed."})
                await self.close(code=4403)
                return
            if not await self.is_superuser():
                await self.send_json({"type": "auth_error", "error": "Only a superuser can host a game."})
                await self.close(code=4403)
                return
            self.role = "host"
            self.token = token
            await self.channel_layer.group_add(f"host_{self.code}", self.channel_name)
            await self.send_json({"type": "authenticated", "role": "host", "state": await self.get_host_state()})
            return

        try:
            player_id = await self.get_player_id(token)
        except (ValueError, Player.DoesNotExist):
            await self.send_json({"type": "auth_error", "error": "Player session is invalid."})
            await self.close(code=4403)
            return
        self.role = "player"
        self.token = token
        self.player_id = player_id
        self.player = await self.get_player(player_id)
        await self.channel_layer.group_add(f"room_{self.code}", self.channel_name)
        await self.channel_layer.group_add(player_group(player_id), self.channel_name)
        await self.set_connected(player_id, True)
        await self.send_json({"type": "authenticated", "role": "player", "state": await self.get_player_state(player_id)})
        # 200 players reconnecting in a burst must not rebuild the full host
        # state 200 times; the throttled broadcast coalesces the churn.
        await database_sync_to_async(broadcast_host_state_throttled)(self.room)

    async def handle_host_action(self, content):
        action = content.get("action")
        if action == "start_game":
            await self.db_action(start_game)
        elif action == "start_fastest":
            await self.db_action(start_fastest_finger)
        elif action == "finish_fastest":
            await self.db_action(finish_fastest_finger)
        elif action == "start_question":
            await self.refresh_room()
            sequence = int(content.get("sequence") or (self.room.current_question_number + 1))
            await self.db_action(start_question, sequence)
        elif action == "lock":
            await self.db_action(lock_question)
        elif action == "reveal":
            await self.db_action(reveal_question)
        elif action == "leaderboard":
            await self.set_leaderboard()
        elif action == "next":
            await self.db_action(advance_after_reveal)
        elif action == "pause":
            await self.db_action(pause_question)
        elif action == "resume":
            await self.db_action(resume_question)
        elif action == "end":
            await self.end_game()
        else:
            raise GameError("Unknown host action.")
        await self.refresh_room()
        # One public room.state per stage change is enough: the question text
        # and status are in the shared state. Rebuilding + pushing 200 private
        # player states here is what delayed questions by 5–6 s under load.
        await self.broadcast_now("host.action")

    async def handle_player_action(self, content):
        action = content.get("action")
        if action == "answer":
            result = await self.db_player_action(submit_answer, content.get("option"))
            await self.send_json({"type": "action.ok", "action": action, "result": {
                "locked": result.locked,
                "selected": result.selected_option,
                "attempts": result.attempts,
                "question_id": result.game_question_id,
                "question_number": result.game_question.sequence,
            }, "question_number": result.game_question.sequence})
        elif action == "lifeline":
            key, payload = await self.db_player_action(use_lifeline, content.get("lifeline"))
            await self.send_json({"type": "action.ok", "action": action, "result": {"lifeline": key, "payload": payload}})
        elif action == "fastest_submit":
            submission = await self.db_player_action(submit_fastest_finger, content.get("answer"))
            await self.send_json({"type": "action.ok", "action": action, "result": {"submitted": True, "correct": submission.is_correct}})
        elif action == "audience_vote":
            poll_id = int(content.get("poll_id"))
            requester_id = await self.vote_poll(poll_id, content.get("option"))
            await self.send_json({"type": "action.ok", "action": action, "result": {}})
            await database_sync_to_async(send_player_state)(requester_id, self.room)
        elif action == "expert_answer":
            request_id = int(content.get("request_id"))
            requester_id, option = await self.answer_expert(request_id, content.get("option"))
            await self.send_json({"type": "action.ok", "action": action, "result": {"option": option}})
            await database_sync_to_async(send_player_state)(requester_id, self.room)
        else:
            raise GameError("Unknown player action.")
        await self.refresh_room()
        if action in {"answer", "fastest_submit"}:
            # High-frequency path: only a small delta (counters) goes to the
            # host. A full state_for_host() per answer is what stalled the
            # host UI while 200 players were answering.
            event = "player.answer" if action == "answer" else "fastest.submitted"
            await database_sync_to_async(broadcast_host_delta)(self.room, event=event)
        elif action == "lifeline":
            # 50:50 is private; Flip changes the shared question and must be broadcast.
            if key == "FLIP":
                await self.broadcast_now("player.lifeline")
            await self.send_private_state()


    async def db_action(self, fn, *args):
        await database_sync_to_async(fn)(self.room, *args)

    async def db_player_action(self, fn, *args):
        return await database_sync_to_async(fn)(self.room, self.player, *args)

    async def broadcast_now(self, event):
        await database_sync_to_async(broadcast_room)(self.room, event=event)

    async def send_private_state(self):
        state = await self.get_player_state(self.player_id)
        await self.send_json({"type": "player.private", "state": state})

    async def disconnect(self, close_code):
        if self.role == "player" and getattr(self, "player_id", None):
            try:
                await self.set_connected(self.player_id, False)
                # A flood of disconnects coalesces into one host snapshot.
                await database_sync_to_async(broadcast_host_state_throttled)(self.room)
            except Exception:
                pass
        if self.role == "player" and getattr(self, "player_id", None):
            await self.channel_layer.group_discard(player_group(self.player_id), self.channel_name)
            await self.channel_layer.group_discard(f"room_{self.code}", self.channel_name)
        if self.role == "host":
            await self.channel_layer.group_discard(f"host_{self.code}", self.channel_name)

    async def room_state(self, event):
        await self.send_json({"type": "room.state", "state": event["payload"]})

    async def host_state_event(self, event):
        await self.send_json({"type": "host.state", "state": event["payload"]})

    async def host_delta_event(self, event):
        await self.send_json({"type": "host.delta", "state": event["payload"]})

    async def player_state_event(self, event):
        await self.send_json({"type": "player.private", "state": event["payload"]})

    @database_sync_to_async
    def get_room(self, code):
        # ``.first()`` keeps an unknown room code from raising DoesNotExist
        # inside connect(); the caller closes the socket with 4404 instead.
        return GameRoom.objects.filter(room_code=code).first()

    @database_sync_to_async
    def refresh_room(self):
        self.room.refresh_from_db()
        return self.room

    @database_sync_to_async
    def is_valid_host(self, token):
        return token and str(token) == str(self.room.host_session)

    @database_sync_to_async
    def is_superuser(self):
        user = self.scope.get("user")
        return bool(user and user.is_authenticated and user.is_superuser)

    @database_sync_to_async
    def get_player_id(self, token):
        sid = uuid.UUID(token)
        return Player.objects.get(room=self.room, session_id=sid).id

    @database_sync_to_async
    def get_player(self, player_id):
        return Player.objects.select_related("room").get(pk=player_id)

    @database_sync_to_async
    def set_connected(self, player_id, connected):
        Player.objects.filter(pk=player_id).update(connected=connected)

    @database_sync_to_async
    def touch_player(self, player_id):
        from django.utils import timezone
        Player.objects.filter(pk=player_id).update(connected=True, last_seen=timezone.now())

    @database_sync_to_async
    def get_player_state(self, player_id):
        player = Player.objects.select_related("room").get(pk=player_id)
        return state_for_player(self.room, player)

    @database_sync_to_async
    def get_host_state(self):
        self.room.refresh_from_db()
        return state_for_host(self.room)

    @database_sync_to_async
    def set_leaderboard(self):
        self.room.refresh_from_db()
        self.room.status = GameRoom.Status.LEADERBOARD
        self.room.save(update_fields=["status"])

    @database_sync_to_async
    def end_game(self):
        from django.utils import timezone
        self.room.refresh_from_db()
        self.room.status = GameRoom.Status.GAME_OVER
        self.room.ended_at = timezone.now()
        self.room.question_deadline = None
        self.room.paused_remaining_seconds = None
        self.room.save(update_fields=["status", "ended_at", "question_deadline", "paused_remaining_seconds"])

    @database_sync_to_async
    def vote_poll(self, poll_id, option):
        poll = AudiencePoll.objects.select_related("requester").get(id=poll_id, game_question__room=self.room)
        vote_audience(poll, self.player, option)
        return poll.requester_id

    @database_sync_to_async
    def answer_expert(self, request_id, option):
        req = ExpertRequest.objects.select_related("requester").get(id=request_id, game_question__room=self.room)
        selected = expert_answer(req, self.player, option)
        return req.requester_id, selected
