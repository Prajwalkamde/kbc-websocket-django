import uuid
from django.db import models


class GameRoom(models.Model):
    class Status(models.TextChoices):
        LOBBY = "LOBBY", "Lobby"
        FASTEST_FINGER = "FASTEST_FINGER", "Fastest Finger"
        QUESTION_ACTIVE = "QUESTION_ACTIVE", "Question Active"
        ANSWER_LOCKED = "ANSWER_LOCKED", "Answer Locked"
        REVEAL = "REVEAL", "Reveal"
        LEADERBOARD = "LEADERBOARD", "Leaderboard"
        FINAL = "FINAL", "Final"
        GAME_OVER = "GAME_OVER", "Game Over"
        PAUSED = "PAUSED", "Paused"

    room_code = models.CharField(max_length=6, unique=True, db_index=True)
    host_session = models.UUIDField(default=uuid.uuid4, unique=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.LOBBY)
    current_question_number = models.PositiveSmallIntegerField(default=0)
    total_questions = models.PositiveSmallIntegerField(default=15)
    timer_seconds = models.PositiveSmallIntegerField(default=15)
    question_deadline = models.DateTimeField(null=True, blank=True)
    paused_remaining_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.room_code


# Fix typo defensively before Django model import on old generated copies.
if not hasattr(models, "PositiveSmallSmallIntegerField"):
    pass


class Question(models.Model):
    class Difficulty(models.TextChoices):
        EASY = "EASY", "Easy"
        MEDIUM = "MEDIUM", "Medium"
        HARD = "HARD", "Hard"
        EXPERT = "EXPERT", "Expert"
        VERY_HARD = "VERY_HARD", "Very Hard"

    question_text = models.TextField()
    option_a = models.CharField(max_length=255)
    option_b = models.CharField(max_length=255)
    option_c = models.CharField(max_length=255)
    option_d = models.CharField(max_length=255)
    correct_option = models.CharField(max_length=1, choices=[("A","A"),("B","B"),("C","C"),("D","D")])
    category = models.CharField(max_length=100)
    difficulty = models.CharField(max_length=20, choices=Difficulty.choices)
    explanation = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    def options(self):
        return {"A": self.option_a, "B": self.option_b, "C": self.option_c, "D": self.option_d}

    def __str__(self):
        return self.question_text[:80]


class GameQuestion(models.Model):
    room = models.ForeignKey(GameRoom, on_delete=models.CASCADE, related_name="game_questions")
    question = models.ForeignKey(Question, on_delete=models.PROTECT)
    sequence = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    revealed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["room", "sequence"], name="unique_room_sequence"),
        ]
        ordering = ["sequence"]


class Player(models.Model):
    room = models.ForeignKey(GameRoom, on_delete=models.CASCADE, related_name="players")
    display_name = models.CharField(max_length=80)
    session_id = models.UUIDField(default=uuid.uuid4)
    current_prize = models.PositiveBigIntegerField(default=0)
    secured_prize = models.PositiveBigIntegerField(default=0)
    correct_answers = models.PositiveSmallIntegerField(default=0)
    answered_questions = models.PositiveSmallIntegerField(default=0)
    missed_questions = models.PositiveSmallIntegerField(default=0)
    total_response_time_ms = models.PositiveBigIntegerField(default=0)
    lifelines = models.JSONField(default=dict)
    connected = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["room", "session_id"], name="unique_player_session"),
        ]

    def __str__(self):
        return self.display_name


class Answer(models.Model):
    game_question = models.ForeignKey(GameQuestion, on_delete=models.CASCADE, related_name="answers")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="answers")
    selected_option = models.CharField(max_length=1, null=True, blank=True)
    attempts = models.JSONField(default=list)
    submitted_at = models.DateTimeField(null=True, blank=True)
    response_time_ms = models.PositiveIntegerField(null=True, blank=True)
    is_correct = models.BooleanField(default=False)
    locked = models.BooleanField(default=False)
    missed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["game_question", "player"], name="unique_answer"),
        ]


class FastestFingerRound(models.Model):
    room = models.OneToOneField(GameRoom, on_delete=models.CASCADE, related_name="fastest_round")
    prompt = models.JSONField(default=dict)
    started_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    locked = models.BooleanField(default=False)


class FastestFingerSubmission(models.Model):
    round = models.ForeignKey(FastestFingerRound, on_delete=models.CASCADE, related_name="submissions")
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    sequence_answer = models.JSONField()
    submitted_at = models.DateTimeField()
    is_correct = models.BooleanField(default=False)
    response_time_ms = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["round", "player"], name="unique_fastest_submission"),
        ]
        indexes = [
            models.Index(fields=["round", "response_time_ms"], name="ff_round_time_idx"),
        ]


class AudiencePoll(models.Model):
    game_question = models.ForeignKey(GameQuestion, on_delete=models.CASCADE, related_name="polls")
    requester = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="polls_requested")
    started_at = models.DateTimeField(auto_now_add=True)
    deadline = models.DateTimeField()
    completed = models.BooleanField(default=False)


class AudienceVote(models.Model):
    poll = models.ForeignKey(AudiencePoll, on_delete=models.CASCADE, related_name="votes")
    voter = models.ForeignKey(Player, on_delete=models.CASCADE)
    selected_option = models.CharField(max_length=1)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["poll", "voter"], name="unique_poll_voter")
        ]


class ExpertRequest(models.Model):
    game_question = models.ForeignKey(GameQuestion, on_delete=models.CASCADE, related_name="expert_requests")
    requester = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="expert_requests_made")
    expert = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="expert_requests_received")
    expert_option = models.CharField(max_length=1, null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class LifelineUse(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="lifeline_uses")
    game_question = models.ForeignKey(GameQuestion, on_delete=models.CASCADE)
    lifeline = models.CharField(max_length=20)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["player", "game_question", "lifeline"], name="unique_lifeline_per_question"),
        ]
        indexes = [
            models.Index(fields=["player", "game_question", "lifeline"], name="life_player_q_idx"),
        ]
