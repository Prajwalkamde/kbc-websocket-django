import uuid
import json
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from game.models import GameRoom, Player, Question
from game.services import (
    GameError, create_or_reconnect_player, prepare_question_set,
    finish_fastest_finger, start_fastest_finger, start_game, start_question,
    submit_answer, submit_fastest_finger, lock_question, pause_question,
    resume_question, reveal_question,
)


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class CoreGameTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.host_user = user_model.objects.create_superuser(
            username="host", email="host@example.com", password="host-password"
        )
        self.regular_user = user_model.objects.create_user(
            username="player", password="player-password"
        )
        self.room = GameRoom.objects.create(room_code="ABC123")
        self.player = create_or_reconnect_player(self.room, "Prajwal", uuid.uuid4())
        diffs = ["EASY"] * 4 + ["MEDIUM"] * 3 + ["HARD"] * 3 + ["EXPERT"] * 3 + ["VERY_HARD"] * 2
        for i, difficulty in enumerate(diffs):
            Question.objects.create(
                question_text=f"Q{i}",
                option_a="A", option_b="B", option_c="C", option_d="D",
                correct_option="B", category="Test", difficulty=difficulty
            )

    def start_first_question(self):
        start_game(self.room)
        finish_fastest_finger(self.room)
        return start_question(self.room, 1)

    def test_prepare_and_start_game(self):
        start_game(self.room)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, GameRoom.Status.FASTEST_FINGER)
        self.assertEqual(self.room.game_questions.count(), 15)
        self.assertIsNotNone(self.room.fastest_round.started_at)
        self.assertEqual(self.room.fastest_round.deadline, None)

    def test_answer_scoring_and_no_elimination(self):
        self.start_first_question()
        submit_answer(self.room, self.player, "B")
        lock_question(self.room)
        reveal_question(self.room)
        self.player.refresh_from_db()
        self.assertEqual(self.player.correct_answers, 1)
        self.assertEqual(self.player.current_prize, 100)

    def test_wrong_answer_keeps_score_unchanged(self):
        self.start_first_question()
        submit_answer(self.room, self.player, "A")
        lock_question(self.room)
        reveal_question(self.room)
        self.player.refresh_from_db()
        self.assertEqual(self.player.current_prize, 0)

    def test_correct_answers_double_current_score(self):
        self.start_first_question()
        for sequence, expected_score in ((1, 100), (2, 200), (3, 400), (4, 800)):
            if sequence > 1:
                start_question(self.room, sequence)
            submit_answer(self.room, self.player, "B")
            lock_question(self.room)
            reveal_question(self.room)
            self.player.refresh_from_db()
            self.assertEqual(self.player.current_prize, expected_score)

    def test_leaderboard_breaks_score_ties_by_response_time(self):
        from game.services import leaderboard

        second_player = create_or_reconnect_player(self.room, "Second", uuid.uuid4())
        self.player.current_prize = second_player.current_prize = 100
        self.player.total_response_time_ms = 2000
        second_player.total_response_time_ms = 1000
        self.player.save(update_fields=["current_prize", "total_response_time_ms"])
        second_player.save(update_fields=["current_prize", "total_response_time_ms"])
        rows = leaderboard(self.room)
        self.assertEqual(rows[0]["name"], "Second")
        self.assertEqual(rows[1]["name"], "Prajwal")

    def test_finishing_fastest_finger_can_start_question(self):
        start_game(self.room)
        finish_fastest_finger(self.room)
        start_question(self.room, 1)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, GameRoom.Status.QUESTION_ACTIVE)
        self.assertEqual(self.room.current_question_number, 1)

    def test_finished_fastest_finger_rejects_late_submission(self):
        start_game(self.room)
        finish_fastest_finger(self.room)
        with self.assertRaises(GameError):
            submit_fastest_finger(self.room, self.player, ["Moon Landing", "World Wide Web", "Internet", "Smartphone"])

    def test_fastest_finger_can_restart_with_fresh_options(self):
        start_game(self.room)
        first_prompt = self.room.fastest_round.prompt
        finish_fastest_finger(self.room)
        start_fastest_finger(self.room)
        self.room.refresh_from_db()
        self.assertFalse(self.room.fastest_round.locked)
        self.assertEqual(self.room.fastest_round.submissions.count(), 0)
        self.assertEqual(len(self.room.fastest_round.prompt["options"]), 4)
        self.assertEqual(
            sum(option == self.room.fastest_round.prompt["correct_order"] for option in self.room.fastest_round.prompt["options"]),
            1,
        )
        self.assertNotEqual(
            self.room.fastest_round.prompt["text"],
            first_prompt["text"],
            "Restarting fastest finger should select a fresh prompt.",
        )

    def test_fastest_finger_state_returns_submitted_order(self):
        from game.game import state_for_player

        start_game(self.room)
        answer = self.room.fastest_round.prompt["options"][0]
        submit_fastest_finger(self.room, self.player, answer)
        state = state_for_player(self.room, self.player)
        self.assertEqual(state["fastest"]["submitted_answer"], answer)
        self.assertTrue(state["fastest"]["submitted"])

    def test_question_timer_is_45_seconds(self):
        self.start_first_question()
        self.room.refresh_from_db()
        self.assertEqual(self.room.timer_seconds, 45)

    def test_duplicate_submission_rejected(self):
        self.start_first_question()
        submit_answer(self.room, self.player, "B")
        with self.assertRaises(GameError):
            submit_answer(self.room, self.player, "A")

    def test_lifeline_endpoints_handle_unknown_player(self):
        headers = {"HTTP_X_PLAYER_SESSION": str(uuid.uuid4())}
        payload = json.dumps({"poll_id": 1, "option": "A"})

        audience_response = self.client.post(
            f"/api/rooms/{self.room.room_code}/audience-vote/",
            data=payload,
            content_type="application/json",
            **headers,
        )
        expert_response = self.client.post(
            f"/api/rooms/{self.room.room_code}/expert-answer/",
            data=json.dumps({"request_id": 1, "option": "A"}),
            content_type="application/json",
            **headers,
        )

        self.assertEqual(audience_response.status_code, 400)
        self.assertEqual(expert_response.status_code, 400)
        self.assertFalse(audience_response.json()["ok"])
        self.assertFalse(expert_response.json()["ok"])

    def test_invalid_room_code_returns_json_error(self):
        response = self.client.post(
            "/api/rooms/join/",
            data=json.dumps({"display_name": "Guest", "room_code": "WRONG1"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"ok": False, "error": "Invalid room code."})

    def test_host_state_includes_joined_player_name(self):
        self.client.force_login(self.host_user)
        response = self.client.get(
            f"/api/rooms/{self.room.room_code}/state/",
            HTTP_X_HOST_SESSION=str(self.room.host_session),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"]["players"][0]["name"], "Prajwal")
        self.assertTrue(response.json()["state"]["fastest"]["can_start"])

    def test_host_requires_superuser_login(self):
        anonymous_page = self.client.get("/host/")
        self.assertEqual(anonymous_page.status_code, 302)
        self.assertIn("/login/", anonymous_page["Location"])

        self.client.force_login(self.regular_user)
        self.assertEqual(self.client.get("/host/").status_code, 403)
        response = self.client.post("/api/rooms/create/")
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.host_user)
        self.assertEqual(self.client.get("/host/").status_code, 200)
        response = self.client.post("/api/rooms/create/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_second_question_starts_after_reveal(self):
        self.start_first_question()
        submit_answer(self.room, self.player, "B")
        lock_question(self.room)
        reveal_question(self.room)

        start_question(self.room, 2)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, GameRoom.Status.QUESTION_ACTIVE)
        self.assertEqual(self.room.current_question_number, 2)

    def test_question_pause_preserves_remaining_time(self):
        self.start_first_question()
        pause_question(self.room)
        self.room.refresh_from_db()
        remaining = self.room.paused_remaining_seconds
        self.assertEqual(self.room.status, GameRoom.Status.PAUSED)
        self.assertIsNone(self.room.question_deadline)
        self.assertGreater(remaining, 0)

        resume_question(self.room)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, GameRoom.Status.QUESTION_ACTIVE)
        self.assertIsNone(self.room.paused_remaining_seconds)
        self.assertIsNotNone(self.room.question_deadline)

    def test_player_state_never_contains_fastest_finger_solution(self):
        from game.game import state_for_player

        lobby_state = state_for_player(self.room, self.player)
        self.assertNotIn("correct_order", lobby_state["fastest"]["prompt"])

        start_game(self.room)
        active_state = state_for_player(self.room, self.player)
        self.assertNotIn("correct_order", active_state["fastest"]["prompt"])

    def test_rest_start_game_action_starts_fastest_finger(self):
        self.client.force_login(self.host_user)
        response = self.client.post(
            f"/api/rooms/{self.room.room_code}/host-action/",
            data=json.dumps({"action": "start_game"}),
            content_type="application/json",
            HTTP_X_HOST_SESSION=str(self.room.host_session),
        )

        self.assertEqual(response.status_code, 200)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, GameRoom.Status.FASTEST_FINGER)

    def test_rest_game_flow_scores_answer_and_reveals(self):
        self.client.force_login(self.host_user)
        host_headers = {"HTTP_X_HOST_SESSION": str(self.room.host_session)}

        for action, body in (
            ("start_game", {}),
            ("finish_fastest", {}),
            ("start_question", {"sequence": 1}),
        ):
            response = self.client.post(
                f"/api/rooms/{self.room.room_code}/host-action/",
                data=json.dumps({"action": action, **body}),
                content_type="application/json",
                **host_headers,
            )
            self.assertEqual(response.status_code, 200)

        answer_response = self.client.post(
            f"/api/rooms/{self.room.room_code}/answer/",
            data=json.dumps({"option": "B"}),
            content_type="application/json",
            HTTP_X_PLAYER_SESSION=str(self.player.session_id),
        )
        self.assertEqual(answer_response.status_code, 200)
        self.assertTrue(answer_response.json()["locked"])

        for action in ("lock", "reveal"):
            response = self.client.post(
                f"/api/rooms/{self.room.room_code}/host-action/",
                data=json.dumps({"action": action}),
                content_type="application/json",
                **host_headers,
            )
            self.assertEqual(response.status_code, 200)

        self.player.refresh_from_db()
        self.room.refresh_from_db()
        self.assertEqual(self.player.current_prize, 100)
        self.assertEqual(self.room.status, GameRoom.Status.REVEAL)

    def test_expired_question_locks_through_scheduled_task(self):
        from game.tasks import process_expired_questions

        self.start_first_question()
        self.room.refresh_from_db()
        self.room.question_deadline = timezone.now() - timezone.timedelta(seconds=1)
        self.room.save(update_fields=["question_deadline"])

        self.assertEqual(process_expired_questions(), 1)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, GameRoom.Status.ANSWER_LOCKED)
