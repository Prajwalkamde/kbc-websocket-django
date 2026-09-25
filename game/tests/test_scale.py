"""Scale behaviors: throttled host broadcasts and Fastest Finger payloads.

* A burst of joins/auths/disconnects must coalesce into ~one host snapshot.
* A full host snapshot must cancel any coalesced update still in flight.
* While Fastest Finger is live the host view carries only the submissions
  count; the ranked results array exists only after the round is locked.
"""
import json
import time
import uuid
from unittest import mock

from django.test import TestCase, TransactionTestCase, override_settings

from game.game import state_for_host
from game.models import GameRoom
from game.realtime import broadcast_host_state, broadcast_host_state_throttled
from game.services import (
    create_or_reconnect_player,
    finish_fastest_finger,
    start_game,
    submit_fastest_finger,
)

CHANNEL_LAYER_OVERRIDE = {
    "CHANNEL_LAYERS": {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    "STORAGES": {
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        }
    },
}


@override_settings(**CHANNEL_LAYER_OVERRIDE)
class ThrottledHostBroadcastTests(TransactionTestCase):
    """TransactionTestCase: the flush timer runs on another thread/connection."""

    def test_burst_coalesces_into_one_snapshot(self):
        room = GameRoom.objects.create(room_code="THROTA")
        with mock.patch("game.realtime._send") as send:
            for _ in range(3):
                broadcast_host_state_throttled(room, interval=0.05)

            self.assertEqual(send.call_count, 0, "no host state immediately")

            deadline = time.monotonic() + 3
            while send.call_count == 0 and time.monotonic() < deadline:
                time.sleep(0.02)

            self.assertEqual(
                send.call_count, 1, "the burst must coalesce into one snapshot"
            )
            group, event, payload = send.call_args.args
            self.assertEqual(event, "host_state_event")
            self.assertEqual(payload["room_code"], "THROTA")
            self.assertEqual(payload["status"], "LOBBY")

    def test_full_state_cancels_pending_throttle(self):
        room = GameRoom.objects.create(room_code="THROTB")
        with mock.patch("game.realtime._send") as send:
            broadcast_host_state_throttled(room, interval=0.3)
            broadcast_host_state(room)
            self.assertEqual(send.call_count, 1, "full state is immediate")

            time.sleep(0.5)
            self.assertEqual(
                send.call_count, 1, "a cancelled throttle must never fire"
            )


@override_settings(**CHANNEL_LAYER_OVERRIDE)
class JoinBroadcastTests(TestCase):
    def setUp(self):
        self.room = GameRoom.objects.create(room_code="SCALE1")

    def test_join_is_throttled_and_session_id_is_authoritative(self):
        session_id = str(uuid.uuid4())
        with mock.patch("game.realtime._send") as send:
            response = self.client.post(
                "/api/rooms/join/",
                data=json.dumps(
                    {
                        "display_name": "Joiner",
                        "room_code": self.room.room_code,
                        "session_id": session_id,
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        # The session_id sent by the client is the player's identity, so 200
        # joins with 200 unique session_ids create 200 players even when the
        # HTTP sessions/cookies are shared.
        self.assertEqual(payload["player_session"], session_id)
        self.assertEqual(
            send.call_count, 0, "a join must not push an immediate full host state"
        )

    def test_distinct_session_ids_create_distinct_players(self):
        ids = [str(uuid.uuid4()) for _ in range(5)]
        for session_id, index in zip(ids, range(len(ids))):
            response = self.client.post(
                "/api/rooms/join/",
                data=json.dumps(
                    {
                        "display_name": f"P{index}",
                        "room_code": self.room.room_code,
                        "session_id": session_id,
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(self.room.players.count(), 5)
        state = state_for_host(self.room)
        self.assertEqual(state["player_count"], 5)


class FastestFingerHostStateTests(TestCase):
    def setUp(self):
        self.room = GameRoom.objects.create(room_code="FFHOST")
        self.player = create_or_reconnect_player(self.room, "Prajwal", uuid.uuid4())
        difficulties = (
            ["EASY"] * 4 + ["MEDIUM"] * 3 + ["HARD"] * 3 + ["EXPERT"] * 3 + ["VERY_HARD"] * 2
        )
        for index, difficulty in enumerate(difficulties):
            from game.models import Question

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

    def test_live_round_sends_submissions_count_only(self):
        start_game(self.room)

        live = state_for_host(self.room)
        self.assertTrue(live["fastest"]["started"])
        self.assertFalse(live["fastest"]["locked"])
        self.assertEqual(live["fastest"]["submissions"], 0)
        self.assertEqual(live["fastest"]["results"], [])

        answer = self.room.fastest_round.prompt["options"][0]
        submit_fastest_finger(self.room, self.player, answer)

        live = state_for_host(self.room)
        self.assertEqual(live["fastest"]["submissions"], 1)
        self.assertEqual(
            live["fastest"]["results"],
            [],
            "ranked results must not be built while the round is live",
        )

    def test_locked_round_sends_full_results(self):
        start_game(self.room)
        prompt = self.room.fastest_round.prompt
        # The correct ordering is one of the four shuffled options shown.
        answer = prompt["correct_order"]
        submit_fastest_finger(self.room, self.player, answer)
        finish_fastest_finger(self.room)

        locked = state_for_host(self.room)
        self.assertTrue(locked["fastest"]["locked"])
        self.assertEqual(locked["fastest"]["submissions"], 1)
        self.assertEqual(locked["fastest"]["results"][0]["name"], "Prajwal")
        self.assertEqual(locked["fastest"]["results"][0]["answer"], answer)
        self.assertTrue(locked["fastest"]["results"][0]["correct"])
