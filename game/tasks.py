from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import GameQuestion, GameRoom
from .realtime import broadcast_room
from .services import GameError, lock_question


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def lock_expired_question(self, room_id, sequence):
    try:
        room = GameRoom.objects.get(pk=room_id)
    except GameRoom.DoesNotExist:
        return False
    if room.status != GameRoom.Status.QUESTION_ACTIVE or room.current_question_number != sequence:
        return False
    if not room.question_deadline or timezone.now() < room.question_deadline:
        return False
    try:
        lock_question(room)
    except GameError:
        return False
    room.refresh_from_db()
    broadcast_room(room, event="timer.expired")
    return True


@shared_task
def process_expired_questions():
    now = timezone.now()
    room_ids = list(
        GameRoom.objects.filter(
            status=GameRoom.Status.QUESTION_ACTIVE,
            question_deadline__isnull=False,
            question_deadline__lte=now,
        ).values_list("id", flat=True)
    )
    locked = 0
    for room_id in room_ids:
        try:
            room = GameRoom.objects.get(pk=room_id)
            if room.status == GameRoom.Status.QUESTION_ACTIVE and room.question_deadline and timezone.now() >= room.question_deadline:
                lock_question(room)
                room.refresh_from_db()
                broadcast_room(room, event="timer.expired")
                locked += 1
        except (GameRoom.DoesNotExist, GameError):
            continue
    return locked
