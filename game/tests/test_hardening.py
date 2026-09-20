"""Tests for the client-side rendering hardening and the Celery timer jobs."""
import re
import uuid
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from game.models import GameRoom, Question
from game.services import (
    create_or_reconnect_player,
    finish_fastest_finger,
    start_game,
    start_question,
)
from game.tasks import lock_expired_question, schedule_deadline_lock

STATIC_JS = Path(settings.BASE_DIR) / "static" / "js"
TEMPLATES = Path(settings.BASE_DIR) / "templates"

CHANNEL_LAYER_OVERRIDE = {
    "CHANNEL_LAYERS": {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    "STORAGES": {
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        }
    },
}

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def strip_comments(source):
    """Remove comments so a docstring mention is not read as real code."""
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", source))


class ClientRenderingSafetyTests(TestCase):
    """The browser code must never hand data to an HTML parser."""

    # innerHTML/outerHTML assignment, insertAdjacentHTML, document.write,
    # eval of a constructed function: all of them turn text into markup.
    UNSAFE = re.compile(
        r"\.\s*(inner|outer)HTML\s*=|insertAdjacentHTML|document\.write|new\s+Function\("
    )

    def test_client_scripts_never_build_html_from_strings(self):
        scripts = sorted(STATIC_JS.glob("*.js"))
        self.assertTrue(scripts, "no client scripts found")
        for path in scripts:
            with self.subTest(script=path.name):
                self.assertIsNone(
                    self.UNSAFE.search(strip_comments(path.read_text())),
                    f"{path.name} still writes raw HTML",
                )

    def test_escape_helper_is_gone(self):
        for name in ("host.js", "player.js"):
            with self.subTest(script=name):
                self.assertNotIn("escapeHtml", (STATIC_JS / name).read_text())

    def test_dom_helper_is_loaded_before_each_view(self):
        helper = (STATIC_JS / "dom.js").read_text()
        self.assertIn("textContent", helper)

        for template, script in (("host.html", "host.js"), ("player.html", "player.js")):
            with self.subTest(template=template):
                markup = (TEMPLATES / template).read_text()
                self.assertIn("js/dom.js", markup)
                self.assertLess(
                    markup.index("js/dom.js"),
                    markup.index(f"js/{script}"),
                    "dom.js must load before the view script",
                )

    def test_room_code_is_escaped_for_javascript(self):
        self.assertIn("|escapejs", (TEMPLATES / "player.html").read_text())


@override_settings(**CHANNEL_LAYER_OVERRIDE)
class PlayerPageRoomCodeTests(TestCase):
    """Room codes are rendered into the page, so they are validated first."""

    def test_valid_room_code_renders_uppercased(self):
        GameRoom.objects.create(room_code="ABC123")
        response = self.client.get("/play/abc123/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'window.ROOM_CODE="ABC123";', html=False)

    def test_malformed_room_codes_are_rejected(self):
        for code in ("AB12", "ABC1234", "ABC-12", "<script>alert(1)</script>"):
            with self.subTest(code=code):
                response = self.client.get(f"/play/{quote(code, safe='')}/")
                self.assertEqual(response.status_code, 404)


@override_settings(**CHANNEL_LAYER_OVERRIDE)
class CeleryDeadlineTests(TestCase):
    def setUp(self):
        self.room = GameRoom.objects.create(room_code="ABC123")
        self.player = create_or_reconnect_player(self.room, "Prajwal", uuid.uuid4())
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

    def start_first_question(self):
        start_game(self.room)
        finish_fastest_finger(self.room)
        start_question(self.room, 1)
        self.room.refresh_from_db()

    def expire_deadline(self):
        self.room.question_deadline = timezone.now() - timezone.timedelta(seconds=1)
        self.room.save(update_fields=["question_deadline"])

    def test_deadline_task_is_not_scheduled_by_default(self):
        with mock.patch.object(lock_expired_question, "apply_async") as apply_async:
            self.start_first_question()
        apply_async.assert_not_called()

    @override_settings(CELERY_DEADLINE_TASKS=True)
    def test_deadline_task_is_queued_with_the_question_deadline(self):
        with mock.patch.object(lock_expired_question, "apply_async") as apply_async:
            self.start_first_question()
        apply_async.assert_called_once()
        kwargs = apply_async.call_args.kwargs
        self.assertEqual(kwargs["args"], [self.room.pk, 1])
        self.assertEqual(kwargs["eta"], self.room.question_deadline)

    @override_settings(CELERY_DEADLINE_TASKS=True)
    def test_broker_failure_does_not_block_starting_a_question(self):
        with mock.patch.object(
            lock_expired_question, "apply_async", side_effect=OSError("broker down")
        ):
            self.start_first_question()
        self.assertEqual(self.room.status, GameRoom.Status.QUESTION_ACTIVE)

    def test_schedule_helper_ignores_rooms_without_a_deadline(self):
        self.assertFalse(schedule_deadline_lock(self.room, 1))

    def test_lock_expired_question_locks_the_matching_question(self):
        self.start_first_question()
        self.expire_deadline()
        self.assertTrue(lock_expired_question(self.room.pk, 1))
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, GameRoom.Status.ANSWER_LOCKED)

    def test_lock_expired_question_ignores_a_stale_sequence(self):
        self.start_first_question()
        self.expire_deadline()
        self.assertFalse(lock_expired_question(self.room.pk, 7))
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, GameRoom.Status.QUESTION_ACTIVE)

    def test_lock_expired_question_ignores_unknown_rooms(self):
        self.assertFalse(lock_expired_question(self.room.pk + 999, 1))

    def test_lock_expired_question_is_idempotent(self):
        self.start_first_question()
        self.expire_deadline()
        self.assertTrue(lock_expired_question(self.room.pk, 1))
        self.assertFalse(lock_expired_question(self.room.pk, 1))
