"""End-to-end WebSocket test for the host + player consumers.

Runs against the real ASGI application with an in-memory channel layer, so the
whole path is covered: session/cookie authentication, the auth handshake, host
actions, private player state and an answer submission.
"""
import asyncio
import uuid

from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from config.asgi import application
from game.models import GameRoom, Question
from game.services import create_or_reconnect_player

CHANNEL_LAYER_OVERRIDE = {
    "CHANNEL_LAYERS": {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    "STORAGES": {
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        }
    },
}


@override_settings(**CHANNEL_LAYER_OVERRIDE)
class PlayerSocketFlowTests(TransactionTestCase):
    def setUp(self):
        self.host_user = get_user_model().objects.create_superuser(
            username="host", email="host@example.com", password="host-password"
        )
        self.room = GameRoom.objects.create(room_code="WS1234")
        self.player = create_or_reconnect_player(self.room, "Socket Player", uuid.uuid4())
        difficulties = (
            ["EASY"] * 4 + ["MEDIUM"] * 3 + ["HARD"] * 3 + ["EXPERT"] * 3 + ["VERY_HARD"] * 2
        )
        for index, difficulty in enumerate(difficulties):
            Question.objects.create(
                question_text=f"Q{index}",
                option_a="A",
                option_b="B",
                option_c="C",
                option_d="D",
                correct_option="B",
                category="Test",
                difficulty=difficulty,
            )
        self.client.force_login(self.host_user)
        self.session_cookie = self.client.cookies["sessionid"].value

    def host_socket(self):
        return WebsocketCommunicator(
            application,
            f"/ws/rooms/{self.room.room_code}/",
            headers=[(b"cookie", f"sessionid={self.session_cookie}".encode())],
        )

    def player_socket(self):
        return WebsocketCommunicator(application, f"/ws/rooms/{self.room.room_code}/")

    async def wait_for(self, communicator, message_type, timeout=5):
        """Read messages until one matches, ignoring unrelated broadcasts."""
        return (await self.wait_for_match(
            communicator,
            lambda message: message.get("type") == message_type,
            timeout=timeout,
            what=message_type,
        ))[0]

    async def wait_for_match(self, communicator, predicate, timeout=5, what=None):
        """Read messages until ``predicate`` matches; return (message, seen_types)."""
        deadline = asyncio.get_event_loop().time() + timeout
        seen = []
        while asyncio.get_event_loop().time() < deadline:
            remaining = max(0.1, deadline - asyncio.get_event_loop().time())
            try:
                message = await communicator.receive_json_from(timeout=remaining)
            except asyncio.TimeoutError:
                break
            seen.append(message.get("type"))
            if predicate(message):
                return message, seen
        raise AssertionError(f"{what or 'match'} not received (saw {seen})")

    async def test_host_and_player_complete_one_question(self):
        host = self.host_socket()
        player = self.player_socket()

        try:
            connected, _ = await host.connect()
            self.assertTrue(connected)
            self.assertEqual((await host.receive_json_from())["type"], "hello")

            await host.send_json_to(
                {"type": "auth", "role": "host", "token": str(self.room.host_session)}
            )
            authenticated = await self.wait_for(host, "authenticated")
            self.assertEqual(authenticated["role"], "host")

            connected, _ = await player.connect()
            self.assertTrue(connected)
            self.assertEqual((await player.receive_json_from())["type"], "hello")

            await player.send_json_to(
                {
                    "type": "auth",
                    "role": "player",
                    "token": str(self.player.session_id),
                }
            )
            authenticated = await self.wait_for(player, "authenticated")
            self.assertEqual(authenticated["role"], "player")
            self.assertEqual(authenticated["state"]["me"]["name"], "Socket Player")

            await host.send_json_to({"type": "action", "action": "start_fastest"})
            await self.wait_for(host, "host.state")

            await host.send_json_to({"type": "action", "action": "finish_fastest"})
            await self.wait_for(host, "host.state")

            await host.send_json_to({"type": "action", "action": "start_question"})
            # Stage changes are now a single public room.state broadcast:
            # the public state carries the question text, so the client can
            # answer without a 200x private-state rebuild per stage change.
            public, seen = await self.wait_for_match(
                player,
                lambda m: m.get("type") == "room.state"
                and m.get("state", {}).get("status") == "QUESTION_ACTIVE",
                what="public room.state with QUESTION_ACTIVE",
            )
            self.assertEqual(public["state"]["question"]["number"], 1)
            # The solution must not leak while the question is open.
            self.assertNotIn("correct_option", public["state"]["question"])
            # No per-stage-change private rebuild reached this player.
            self.assertNotIn("player.private", seen)

            await player.send_json_to(
                {"type": "action", "action": "answer", "option": "B"}
            )
            result = await self.wait_for(player, "action.ok")
            self.assertEqual(result["action"], "answer")
            self.assertTrue(result["result"]["locked"])
            self.assertEqual(result["result"]["question_number"], 1)

            # The host sees the live A/B/C/D counters update.
            delta = await self.wait_for(host, "host.delta")
            self.assertEqual(delta["state"]["event"], "player.answer")
            self.assertEqual(delta["state"]["answers"]["submitted"], 1)
        finally:
            await host.disconnect()
            await player.disconnect()


@override_settings(**CHANNEL_LAYER_OVERRIDE)
class UnknownRoomSocketTests(TransactionTestCase):
    async def test_unknown_room_code_closes_instead_of_crashing(self):
        communicator = WebsocketCommunicator(application, "/ws/rooms/ZZZZZZ/")
        connected, code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4404)
