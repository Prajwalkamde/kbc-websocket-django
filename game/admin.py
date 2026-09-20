from django.contrib import admin
from .models import (
    Answer, AudiencePoll, AudienceVote, ExpertRequest, FastestFingerRound,
    FastestFingerSubmission, GameQuestion, GameRoom, LifelineUse, Player, Question,
)

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("question_text", "category", "difficulty", "correct_option", "active")
    list_filter = ("category", "difficulty", "active")
    search_fields = ("question_text", "option_a", "option_b", "option_c", "option_d")

@admin.register(GameRoom)
class GameRoomAdmin(admin.ModelAdmin):
    list_display = ("room_code", "status", "current_question_number", "created_at")

admin.site.register([
    Player, GameQuestion, Answer, FastestFingerRound, FastestFingerSubmission,
    AudiencePoll, AudienceVote, ExpertRequest, LifelineUse
])
