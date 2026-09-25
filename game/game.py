from django.db.models import Count, Q
from django.utils import timezone

from .constants import FASTEST_PROMPT, PRIZE_LADDER, SAFE_LEVELS
from .models import Answer, AudiencePoll, ExpertRequest, FastestFingerRound, LifelineUse
from .services import leaderboard, question_public


def _current_question(room):
    if not room.current_question_number:
        return None
    return (
        room.game_questions
        .select_related("question")
        .filter(sequence=room.current_question_number)
        .first()
    )


def _fastest_public(ff):
    prompt = dict((ff.prompt if ff else FASTEST_PROMPT) or {})
    prompt.pop("correct_order", None)
    return {
        "prompt": prompt,
        "started": bool(ff and ff.started_at),
        "locked": bool(ff and ff.locked),
    }


def public_room_state(room):
    """Small state safe to broadcast to every connected player."""
    gq = _current_question(room)
    reveal = room.status in {room.Status.REVEAL, room.Status.FINAL, room.Status.GAME_OVER}
    ff = FastestFingerRound.objects.filter(room=room).first()
    player_counts = room.players.aggregate(
        total=Count("id"),
        connected=Count("id", filter=Q(connected=True)),
    )
    state = {
        "room_code": room.room_code,
        "status": room.status,
        "current_question": room.current_question_number,
        "total_questions": room.total_questions,
        "timer_seconds": room.timer_seconds,
        "deadline": room.question_deadline.isoformat() if room.question_deadline else None,
        "paused_remaining_seconds": room.paused_remaining_seconds,
        "player_count": player_counts["total"],
        "connected_count": player_counts["connected"],
        "question": question_public(gq, include_correct=reveal) if gq else None,
        "fastest": _fastest_public(ff),
    }
    if room.current_question_number >= room.total_questions and room.status in {room.Status.FINAL, room.Status.GAME_OVER}:
        state["final_results"] = final_results(room)
    else:
        state["final_results"] = []
    return state


def common_room_state(room):
    """Backward-compatible host/full state."""
    players = list(room.players.all())
    state = public_room_state(room)
    state.update({
        "timer_seconds": room.timer_seconds,
        "player_count": len(players),
        "connected_count": sum(bool(p.connected) for p in players),
        "players": [
            {"id": p.id, "name": p.display_name, "connected": p.connected, "prize": p.current_prize}
            for p in players
        ],
        "leaderboard": leaderboard(room),
    })
    if room.current_question_number >= room.total_questions and room.status in {
        room.Status.REVEAL, room.Status.FINAL, room.Status.GAME_OVER,
    }:
        state["final_results"] = final_results(room)
    else:
        state["final_results"] = []
    return state


def final_results(room):
    players = list(room.players.all().order_by("display_name"))
    answers = list(
        Answer.objects.filter(game_question__room=room, is_correct=True)
        .values_list("player_id", "game_question__sequence")
        .order_by("game_question__sequence")
    )
    correct_by_player = {}
    for player_id, sequence in answers:
        correct_by_player.setdefault(player_id, []).append(sequence)
    results = []
    all_numbers = set(range(1, room.total_questions + 1))
    for player in players:
        correct = correct_by_player.get(player.id, [])
        results.append({
            "name": player.display_name,
            "score": player.current_prize,
            "correct": correct,
            "wrong": sorted(all_numbers - set(correct)),
            "time_ms": player.total_response_time_ms,
        })
    return results


def state_for_host(room):
    state = common_room_state(room)
    state["prize_ladder"] = PRIZE_LADDER
    state["safe_levels"] = list(SAFE_LEVELS)
    ff = FastestFingerRound.objects.filter(room=room).first()
    if ff:
        can_start = (
            room.status in {
                room.Status.LOBBY,
                room.Status.FASTEST_FINGER,
            }
            and (not ff.started_at or ff.locked)
        )
        if bool(ff.started_at) and not ff.locked:
            # While the round is live, 200 players are submitting back to
            # back. The host only needs the running count; ranking every
            # submission on every update is what froze the host UI. The full
            # result table is assembled once, when the round is locked.
            state["fastest"] = {
                "prompt": ff.prompt or FASTEST_PROMPT,
                "started": True,
                "locked": False,
                "can_start": can_start,
                "deadline": ff.deadline.isoformat() if ff.deadline else None,
                "submissions": ff.submissions.count(),
                "results": [],
            }
        else:
            submissions = list(ff.submissions.select_related("player").order_by("response_time_ms", "submitted_at"))
            state["fastest"] = {
                "prompt": ff.prompt or FASTEST_PROMPT,
                "started": bool(ff.started_at),
                "locked": ff.locked,
                "can_start": can_start,
                "deadline": ff.deadline.isoformat() if ff.deadline else None,
                "submissions": len(submissions),
                "results": [
                    {
                        "name": submission.player.display_name,
                        "answer": submission.sequence_answer,
                        "correct": submission.is_correct,
                        "time": submission.response_time_ms,
                    }
                    for submission in submissions
                ],
            }
    else:
        state["fastest"] = {
            **state["fastest"],
            "can_start": room.status == room.Status.LOBBY and bool(state["players"]),
            "deadline": None,
            "submissions": 0,
            "results": [],
        }
    gq = _current_question(room)
    if gq:
        answers = list(Answer.objects.filter(game_question=gq).only("selected_option", "attempts", "locked"))
        selected = [a.selected_option for a in answers if a.selected_option]
        state["answers"] = {
            "submitted": len(selected),
            "locked": sum(bool(a.locked) for a in answers),
            "distribution": {letter: selected.count(letter) for letter in "ABCD"},
        }
    else:
        state["answers"] = {"submitted": 0, "locked": 0, "distribution": {letter: 0 for letter in "ABCD"}}
    return state


def state_for_player(room, player):
    state = public_room_state(room)
    gq = _current_question(room)
    state["me"] = {
        "id": player.id,
        "name": player.display_name,
        "prize": player.current_prize,
        "secured_prize": player.secured_prize,
        "correct_answers": player.correct_answers,
        "answered_questions": player.answered_questions,
        "missed_questions": player.missed_questions,
        "lifelines": player.lifelines,
    }
    state["final_results"] = []
    if gq:
        reveal = room.status in {room.Status.REVEAL, room.Status.FINAL, room.Status.GAME_OVER}
        state["question"] = question_public(gq, include_correct=reveal)
        answer = Answer.objects.filter(game_question=gq, player=player).first()
        state["my_answer"] = {
            "attempts": answer.attempts if answer else [],
            "locked": bool(answer and answer.locked),
            "selected": answer.selected_option if answer else None,
            # A player must not learn whether their answer is correct until the
            # host reveals the question to the room.
            "correct": answer.is_correct if answer and reveal else None,
            "question_id": gq.id,
            "question_number": gq.sequence,
        }
        latest_5050 = LifelineUse.objects.filter(
            player=player, game_question=gq, lifeline="50_50"
        ).order_by("-created_at").first()
        state["removed_options"] = latest_5050.payload.get("removed", []) if latest_5050 else []

        own_poll = AudiencePoll.objects.filter(game_question=gq, requester=player).order_by("-started_at").first()
        if own_poll and timezone.now() >= own_poll.deadline and not own_poll.completed:
            from .services import finalize_poll
            finalize_poll(own_poll)
            own_poll.refresh_from_db()
        if own_poll and own_poll.completed:
            counts = {letter: own_poll.votes.filter(selected_option=letter).count() for letter in "ABCD"}
            total = sum(counts.values())
            percentages = {letter: round(counts[letter] * 100 / total) if total else 0 for letter in "ABCD"}
            state["active_poll"] = {
                "id": own_poll.id, "deadline": own_poll.deadline.isoformat(), "completed": True,
                "can_vote": False, "requester": True, "counts": counts, "percentages": percentages,
            }
        else:
            available_poll = AudiencePoll.objects.filter(
                game_question=gq, completed=False
            ).exclude(requester=player).select_related("requester").order_by("-started_at").first()
            if available_poll:
                state["active_poll"] = {
                    "id": available_poll.id,
                    "deadline": available_poll.deadline.isoformat(),
                    "completed": available_poll.completed,
                    "can_vote": not available_poll.votes.filter(voter=player).exists(),
                    "requester_name": available_poll.requester.display_name,
                    "requester": False,
                }
            else:
                state["active_poll"] = None

        expert = ExpertRequest.objects.filter(
            game_question=gq, requester=player
        ).select_related("expert").order_by("-created_at").first()
        if expert:
            state["expert"] = {
                "request_id": expert.id,
                "expert_name": expert.expert.display_name,
                "answer": expert.expert_option,
                "answered": bool(expert.answered_at),
            }
            state["expert_incoming"] = None
        else:
            incoming = ExpertRequest.objects.filter(
                game_question=gq, expert=player
            ).select_related("requester").order_by("-created_at").first()
            state["expert"] = None
            state["expert_incoming"] = {
                "request_id": incoming.id,
                "question": question_public(gq),
                "answered": bool(incoming.answered_at),
            } if incoming else None
    else:
        state["question"] = None
        state["my_answer"] = None
        state["active_poll"] = None
        state["expert"] = None
        state["expert_incoming"] = None
        state["removed_options"] = []

    ff = FastestFingerRound.objects.filter(room=room).first()
    player_submission = ff.submissions.filter(player=player).first() if ff else None
    private_fastest = _fastest_public(ff)
    state["fastest"] = {
        **private_fastest,
        "started": bool(ff and ff.started_at),
        "locked": bool(ff and ff.locked),
        "deadline": ff.deadline.isoformat() if ff and ff.deadline else None,
        "submitted": bool(player_submission),
        "submitted_answer": player_submission.sequence_answer if player_submission else None,
        "submitted_correct": player_submission.is_correct if player_submission else None,
    }
    if room.current_question_number >= room.total_questions and room.status in {
        room.Status.FINAL, room.Status.GAME_OVER,
    }:
        state["final_results"] = [r for r in final_results(room) if r["name"] == player.display_name]
    return state
