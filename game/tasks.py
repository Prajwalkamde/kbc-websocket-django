"""Celery jobs that keep the question timer authoritative.

An expired question must never stay open, so two layers cooperate:

* ``lock_expired_question`` is queued with an ETA the moment the host starts a
  question, so answers lock exactly at that deadline.
* ``process_expired_questions`` runs every second from Celery beat and catches
  anything the ETA job missed (worker restart, broker downtime, clock drift).

Both jobs re-read the room before locking, so a stale or duplicated task can
never lock the wrong question.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from .models import GameRoom
from .realtime import broadcast_room
from .services import GameError, lock_question

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(DatabaseError,),
    retry_backoff=True,
    max_retries=3,
)
def lock_expired_question(self, room_id, sequence):
    """Lock ``sequence`` once the room deadline for it has passed.

    Returns ``True`` only when this call actually locked the question, which
    makes the task idempotent: late or repeated deliveries return ``False``.
    """
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


def schedule_deadline_lock(room, sequence):
    """Queue the ETA lock task for a freshly started question.

    Kept behind the ``CELERY_DEADLINE_TASKS`` setting so test runs and
    WebSocket-only deployments never need a reachable broker; Celery beat is
    still the safety net when this returns ``False``.
    """
    if not getattr(settings, "CELERY_DEADLINE_TASKS", False):
        return False
    if not room.question_deadline:
        return False

    try:
        lock_expired_question.apply_async(
            args=[room.pk, sequence],
            eta=room.question_deadline,
        )
    except Exception as exc:  # broker unreachable — beat still covers the deadline
        logger.warning(
            "Could not schedule the deadline lock for room %s: %s",
            room.room_code,
            exc,
        )
        return False
    return True


@shared_task
def process_expired_questions():
    """Celery beat safety net: lock every question whose deadline has passed."""
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
        except GameRoom.DoesNotExist:
            continue
        if (
            room.status == GameRoom.Status.QUESTION_ACTIVE
            and room.question_deadline
            and timezone.now() >= room.question_deadline
        ):
            try:
                lock_question(room)
            except GameError:
                continue
            room.refresh_from_db()
            broadcast_room(room, event="timer.expired")
            locked += 1
    return locked
