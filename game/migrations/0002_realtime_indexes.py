from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("game", "0001_initial")]

    operations = [
        migrations.AddConstraint(
            model_name="fastestfingersubmission",
            constraint=models.UniqueConstraint(
                fields=("round", "player"), name="unique_fastest_submission"
            ),
        ),
        migrations.AddIndex(
            model_name="fastestfingersubmission",
            index=models.Index(fields=["round", "response_time_ms"], name="ff_round_time_idx"),
        ),
        migrations.AddConstraint(
            model_name="lifelineuse",
            constraint=models.UniqueConstraint(
                fields=("player", "game_question", "lifeline"),
                name="unique_lifeline_per_question",
            ),
        ),
        migrations.AddIndex(
            model_name="lifelineuse",
            index=models.Index(fields=["player", "game_question", "lifeline"], name="life_player_q_idx"),
        ),
    ]
