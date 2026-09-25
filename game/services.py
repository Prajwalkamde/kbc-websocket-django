import math
import random
import string
from django.db import IntegrityError, transaction
from django.utils import timezone

from .constants import (
    DEFAULT_LIFELINES, DIFFICULTY_BY_SEQUENCE, FASTEST_PROMPTS,
    QUESTION_TIMERS,
)
from .models import (
    Answer, AudiencePoll, AudienceVote, FastestFingerRound,
    FastestFingerSubmission, GameQuestion, GameRoom, LifelineUse, Player, Question,
)


class GameError(Exception):
    pass


def generate_room_code():
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(100):
        code = "".join(random.choice(alphabet) for _ in range(6))
        if not GameRoom.objects.filter(room_code=code).exists():
            return code
    raise GameError("Unable to generate a room code.")


@transaction.atomic
def create_room():
    return GameRoom.objects.create(room_code=generate_room_code())


@transaction.atomic
def create_or_reconnect_player(room, name, session_id):
    name = " ".join(name.split()).strip()
    if not name:
        raise GameError("Name is required.")
    player, created = Player.objects.get_or_create(
        room=room,
        session_id=session_id,
        defaults={"display_name": name[:80], "lifelines": DEFAULT_LIFELINES.copy()},
    )
    if player.display_name != name[:80]:
        player.display_name = name[:80]
    player.connected = True
    player.save()
    return player


@transaction.atomic
def prepare_question_set(room):
    GameQuestion.objects.filter(room=room).delete()
    selected_ids = set()
    created = []
    for seq in range(1, room.total_questions + 1):
        difficulty = DIFFICULTY_BY_SEQUENCE[seq]
        qs = list(Question.objects.filter(active=True, difficulty=difficulty))
        available = [q for q in qs if q.id not in selected_ids]
        if not available:
            raise GameError(f"Not enough active {difficulty} questions. Add more in Django Admin.")
        question = random.choice(available)
        selected_ids.add(question.id)
        created.append(GameQuestion.objects.create(room=room, question=question, sequence=seq))
    return created


def question_public(gq, include_correct=False):
    q = gq.question
    payload = {
        "id": gq.id,
        "number": gq.sequence,
        "text": q.question_text,
        "options": q.options(),
        "category": q.category,
        "difficulty": q.difficulty,
        "started_at": gq.started_at.isoformat() if gq.started_at else None,
    }
    if include_correct:
        payload["correct_option"] = q.correct_option
        payload["explanation"] = q.explanation
    return payload


def prize_for_question(number):
    return 100 * (2 ** (number - 1))


def fastest_prompt(exclude_text=None):
    choices = [prompt for prompt in FASTEST_PROMPTS if prompt["text"] != exclude_text]
    source = random.choice(choices or FASTEST_PROMPTS)
    items = list(source["items"])
    random.shuffle(items)
    correct_order = list(source["correct_order"])
    options = [
        correct_order,
        [correct_order[1], correct_order[0], correct_order[2], correct_order[3]],
        [correct_order[0], correct_order[2], correct_order[1], correct_order[3]],
        [correct_order[0], correct_order[1], correct_order[3], correct_order[2]],
    ]
    random.shuffle(options)
    return {
        "text": source["text"],
        "items": items,
        "options": options,
        "correct_order": correct_order,
    }


def active_poll(room, player):
    gq = room.game_questions.filter(sequence=room.current_question_number).first()
    if not gq:
        return None
    return AudiencePoll.objects.filter(
        game_question=gq, requester=player, completed=False
    ).order_by("-started_at").first()


def apply_score(room, gq):
    players = list(room.players.all())
    answers = {a.player_id: a for a in Answer.objects.filter(game_question=gq)}
    for player in players:
        answer = answers.get(player.id)
        player.answered_questions += 1
        if not answer or answer.missed:
            player.missed_questions += 1
        elif answer.is_correct:
            player.correct_answers += 1
            player.current_prize = player.current_prize * 2 or 100
            player.total_response_time_ms += answer.response_time_ms or 0
        elif answer.response_time_ms:
            player.total_response_time_ms += answer.response_time_ms
    if players:
        Player.objects.bulk_update(
            players,
            ["answered_questions", "missed_questions", "correct_answers", "current_prize", "total_response_time_ms"],
            batch_size=200,
        )
    gq.revealed_at = timezone.now()
    gq.save(update_fields=["revealed_at"])
    room.status = GameRoom.Status.REVEAL
    room.question_deadline = None
    room.save(update_fields=["status", "question_deadline"])


def leaderboard(room):
    rows = room.players.order_by(
        "-current_prize", "total_response_time_ms", "display_name"
    )
    return [{
        "rank": index,
        "id": p.id,
        "name": p.display_name,
        "prize": p.current_prize,
        "correct": p.correct_answers,
        "answered": p.answered_questions,
        "missed": p.missed_questions,
        "time_ms": p.total_response_time_ms,
        "connected": p.connected,
    } for index, p in enumerate(rows, start=1)]


@transaction.atomic
def start_fastest_finger(room):
    room = GameRoom.objects.select_for_update().get(pk=room.pk)

    # Directly start Fastest Finger from Lobby
    if room.status == GameRoom.Status.LOBBY:

        if not room.players.exists():
            raise GameError("At least one player must join.")

        # Prepare normal question set
        prepare_question_set(room)

        # Move room into Fastest Finger stage
        room.status = GameRoom.Status.FASTEST_FINGER
        room.started_at = timezone.now()
        room.current_question_number = 0
        room.save(
            update_fields=[
                "status",
                "started_at",
                "current_question_number",
            ]
        )

    elif room.status != GameRoom.Status.FASTEST_FINGER:
        raise GameError(
            "Fastest Finger cannot be started at this stage."
        )

    # Get/create Fastest Finger round
    ff, created = FastestFingerRound.objects.get_or_create(
        room=room,
        defaults={
            "prompt": fastest_prompt(),
            "started_at": None,
            "deadline": None,
            "locked": False,
        },
    )

    now = timezone.now()

    if ff.started_at is not None and not ff.locked:
        raise GameError("Fastest Finger has already started.")

    if ff.locked:
        # A host may run the round again before Question 1 starts.  A previous
        # submission must never carry over to the new prompt.
        previous_text = (ff.prompt or {}).get("text")
        ff.submissions.all().delete()
        ff.prompt = fastest_prompt(exclude_text=previous_text)

    ff.started_at = now
    ff.deadline = None
    ff.locked = False
    ff.save(update_fields=["prompt", "started_at", "deadline", "locked"])

    return ff


def start_game(room):
    """Backward-compatible host action: start a Fastest Finger round."""
    return start_fastest_finger(room)

@transaction.atomic
def submit_fastest_finger(room, player, sequence_answer):
    room = GameRoom.objects.get(pk=room.pk)
    if room.status != GameRoom.Status.FASTEST_FINGER:
        raise GameError("Fastest Finger is not accepting answers.")
    ff = FastestFingerRound.objects.get(room=room)
    if ff.locked or not ff.started_at:
        raise GameError("Fastest Finger is not accepting answers.")
    now = timezone.now()
    submitted = list(sequence_answer or [])
    correct = submitted == ff.prompt["correct_order"]
    ms = max(0, int((now - ff.started_at).total_seconds() * 1000))
    submission, created = FastestFingerSubmission.objects.get_or_create(
        round=ff, player=player,
        defaults={
            "sequence_answer": submitted, "submitted_at": now,
            "is_correct": correct, "response_time_ms": ms,
        },
    )
    if not created:
        raise GameError("You already submitted.")
    return submission


@transaction.atomic
def finish_fastest_finger(room):
    room = GameRoom.objects.select_for_update().get(pk=room.pk)

    if room.status != GameRoom.Status.FASTEST_FINGER:
        raise GameError("Fastest Finger cannot be finished at this stage.")

    ff = FastestFingerRound.objects.select_for_update().get(room=room)

    if not ff.started_at:
        raise GameError("Fastest Finger has not been started.")

    if ff.locked:
        return ff

    ff.locked = True
    ff.save(update_fields=["locked"])

    return ff


@transaction.atomic
def start_question(room, sequence):
    room = GameRoom.objects.select_for_update().get(pk=room.pk)
    if room.status not in {
        GameRoom.Status.FASTEST_FINGER,
        GameRoom.Status.LEADERBOARD,
        GameRoom.Status.REVEAL,
    }:
        raise GameError("Question cannot start from the current state.")
    if sequence < 1 or sequence > room.total_questions:
        raise GameError("Invalid question number.")
    if sequence != room.current_question_number + 1:
        raise GameError("Questions must be started in order.")

    if room.status == GameRoom.Status.FASTEST_FINGER:
        ff = FastestFingerRound.objects.select_for_update().get(room=room)
        if not ff.started_at:
            raise GameError("Start Fastest Finger first.")
        if not ff.locked:
            raise GameError("Finish Fastest Finger before starting Question 1.")

    gq = GameQuestion.objects.select_for_update().get(room=room, sequence=sequence)
    now = timezone.now()
    room.current_question_number = sequence
    room.timer_seconds = QUESTION_TIMERS[sequence]
    room.question_deadline = now + timezone.timedelta(seconds=room.timer_seconds)
    room.paused_remaining_seconds = None
    room.status = GameRoom.Status.QUESTION_ACTIVE
    room.save(update_fields=[
        "current_question_number", "timer_seconds", "question_deadline",
        "paused_remaining_seconds", "status",
    ])
    gq.started_at = now
    gq.locked_at = None
    gq.revealed_at = None
    gq.save(update_fields=["started_at", "locked_at", "revealed_at"])

    # Local import avoids a circular import: game.tasks imports this module.
    from .tasks import schedule_deadline_lock

    schedule_deadline_lock(room, sequence)
    return gq


@transaction.atomic
def submit_answer(room, player, option):
    room = GameRoom.objects.get(pk=room.pk)
    if room.status != GameRoom.Status.QUESTION_ACTIVE:
        raise GameError("Question is not accepting answers.")
    gq = GameQuestion.objects.select_related("question").get(
        room=room, sequence=room.current_question_number
    )
    now = timezone.now()
    if room.question_deadline and now >= room.question_deadline:
        raise GameError("Time is up.")
    option = str(option or "").upper()
    if option not in {"A", "B", "C", "D"}:
        raise GameError("Invalid option.")

    # No select_for_update: 200 simultaneous answers must not queue behind a
    # locked row. A plain get_or_create is enough because
    # (game_question, player) is unique; the rare concurrent duplicate is
    # recovered from the IntegrityError instead of holding a lock.
    try:
        answer, _ = Answer.objects.get_or_create(
            game_question=gq, player=player, defaults={"attempts": []}
        )
    except IntegrityError:
        answer = Answer.objects.get(game_question=gq, player=player)

    if answer.locked:
        raise GameError("Answer already locked.")
    attempts = list(answer.attempts or [])
    if option in attempts:
        raise GameError("You already used that option.")
    attempts.append(option)
    answer.attempts = attempts
    answer.selected_option = option
    answer.submitted_at = now
    answer.response_time_ms = max(0, int((now - gq.started_at).total_seconds() * 1000))
    answer.is_correct = option == gq.question.correct_option
    # Keep the historical Double Dip behavior if enabled in older room/player data.
    double_dip_enabled = bool(player.lifelines.get("DOUBLE_DIP", False))
    if answer.is_correct or len(attempts) >= (2 if double_dip_enabled else 1):
        answer.locked = True
    answer.save()
    return answer


@transaction.atomic
def use_lifeline(room, player, lifeline):
    room = GameRoom.objects.get(pk=room.pk)
    if room.status != GameRoom.Status.QUESTION_ACTIVE:
        raise GameError("Lifelines are available only during an active question.")
    key = str(lifeline or "").upper()
    locked_player = Player.objects.select_for_update().get(pk=player.pk)
    if key not in {"50_50", "FLIP"} or not locked_player.lifelines.get(key, False):
        raise GameError("Lifeline is unavailable or already used.")
    gq = GameQuestion.objects.select_related("question").get(
        room=room, sequence=room.current_question_number
    )
    if LifelineUse.objects.filter(player=locked_player, game_question=gq, lifeline=key).exists():
        raise GameError("Lifeline already used for this question.")
    payload = {}
    locked_player.lifelines[key] = False

    if key == "50_50":
        wrong = [letter for letter in "ABCD" if letter != gq.question.correct_option]
        payload["removed"] = random.sample(wrong, 2)
    elif key == "FLIP":
        difficulty = gq.question.difficulty
        used = set(room.game_questions.values_list("question_id", flat=True))
        candidates = list(Question.objects.filter(active=True, difficulty=difficulty).exclude(id__in=used))
        if not candidates:
            raise GameError("No replacement question is available.")
        replacement = random.choice(candidates)
        gq.question = replacement
        gq.save(update_fields=["question"])
        payload["question"] = question_public(gq)

    locked_player.save(update_fields=["lifelines"])
    LifelineUse.objects.create(player=locked_player, game_question=gq, lifeline=key, payload=payload)
    return key, payload


@transaction.atomic
def vote_audience(poll, voter, option):
    if poll.completed:
        raise GameError("Audience poll is closed.")
    if timezone.now() > poll.deadline:
        poll.completed = True
        poll.save(update_fields=["completed"])
        raise GameError("Audience poll time is over.")
    if voter.id == poll.requester_id:
        raise GameError("Requester does not vote in their own poll.")
    option = str(option or "").upper()
    if option not in {"A", "B", "C", "D"}:
        raise GameError("Invalid option.")
    vote, created = AudienceVote.objects.get_or_create(
        poll=poll, voter=voter, defaults={"selected_option": option}
    )
    if not created:
        raise GameError("You already voted.")
    return vote


@transaction.atomic
def finalize_poll(poll):
    if not poll.completed and timezone.now() >= poll.deadline:
        poll.completed = True
        poll.save(update_fields=["completed"])
    votes = list(poll.votes.all())
    counts = {letter: 0 for letter in "ABCD"}
    for vote in votes:
        counts[vote.selected_option] += 1
    total = sum(counts.values())
    percentages = {
        letter: round((counts[letter] / total) * 100) if total else 0
        for letter in "ABCD"
    }
    return counts, percentages


@transaction.atomic
def expert_answer(request, expert, option):
    if request.expert_id != expert.id or request.answered_at:
        raise GameError("Expert response is not available.")
    option = str(option or "").upper()
    if option not in {"A", "B", "C", "D"}:
        raise GameError("Invalid option.")
    request.expert_option = option
    request.answered_at = timezone.now()
    request.save(update_fields=["expert_option", "answered_at"])
    return option


@transaction.atomic
def lock_question(room):
    room = GameRoom.objects.select_for_update().get(pk=room.pk)
    if room.status != GameRoom.Status.QUESTION_ACTIVE:
        raise GameError("Question is not active.")
    gq = GameQuestion.objects.select_for_update().get(
        room=room, sequence=room.current_question_number
    )
    now = timezone.now()
    players = list(room.players.all())
    existing = {a.player_id: a for a in Answer.objects.filter(game_question=gq)}
    missing = [
        Answer(game_question=gq, player=player, locked=True, missed=True)
        for player in players if player.id not in existing
    ]
    if missing:
        Answer.objects.bulk_create(missing, batch_size=200)
    to_update = []
    for answer in existing.values():
        if not answer.locked:
            answer.locked = True
            answer.missed = not bool(answer.attempts)
            answer.is_correct = bool(answer.is_correct and answer.selected_option)
            to_update.append(answer)
    if to_update:
        Answer.objects.bulk_update(to_update, ["locked", "missed", "is_correct"], batch_size=200)
    gq.locked_at = now
    gq.save(update_fields=["locked_at"])
    room.status = GameRoom.Status.ANSWER_LOCKED
    room.question_deadline = None
    room.paused_remaining_seconds = None
    room.save(update_fields=["status", "question_deadline", "paused_remaining_seconds"])
    return gq


@transaction.atomic
def reveal_question(room):
    room = GameRoom.objects.select_for_update().get(pk=room.pk)
    if room.status != GameRoom.Status.ANSWER_LOCKED:
        raise GameError("Answers must be locked first.")
    gq = GameQuestion.objects.select_for_update().get(room=room, sequence=room.current_question_number)
    apply_score(room, gq)
    return gq


@transaction.atomic
def advance_after_reveal(room):
    room = GameRoom.objects.select_for_update().get(pk=room.pk)
    if room.status not in {GameRoom.Status.REVEAL, GameRoom.Status.LEADERBOARD}:
        raise GameError("Cannot advance from current state.")
    if room.current_question_number >= room.total_questions:
        room.status = GameRoom.Status.FINAL
    else:
        room.status = GameRoom.Status.LEADERBOARD
    room.save(update_fields=["status"])
    return room


def maybe_timeout(room):
    if room.status == GameRoom.Status.QUESTION_ACTIVE and room.question_deadline:
        if timezone.now() >= room.question_deadline:
            lock_question(room)
            return True
    return False


@transaction.atomic
def pause_question(room):
    """Pause an active question without allowing its timer to keep running."""
    room = GameRoom.objects.select_for_update().get(pk=room.pk)
    if room.status != GameRoom.Status.QUESTION_ACTIVE or not room.question_deadline:
        raise GameError("Only an active timed question can be paused.")

    now = timezone.now()
    remaining = math.ceil((room.question_deadline - now).total_seconds())
    if remaining <= 0:
        raise GameError("Time is up. Lock the answers instead.")

    room.status = GameRoom.Status.PAUSED
    room.paused_remaining_seconds = remaining
    room.question_deadline = None
    room.save(update_fields=["status", "paused_remaining_seconds", "question_deadline"])
    return room


@transaction.atomic
def resume_question(room):
    """Resume a question using the remaining time captured when it was paused."""
    room = GameRoom.objects.select_for_update().get(pk=room.pk)
    if room.status != GameRoom.Status.PAUSED:
        raise GameError("The question is not paused.")
    if not room.paused_remaining_seconds:
        raise GameError("Paused question has no remaining timer.")

    room.status = GameRoom.Status.QUESTION_ACTIVE
    room.question_deadline = timezone.now() + timezone.timedelta(
        seconds=room.paused_remaining_seconds
    )
    room.paused_remaining_seconds = None
    room.save(update_fields=["status", "question_deadline", "paused_remaining_seconds"])
    return room
