from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("game", "0002_realtime_indexes")]

    operations = [
        migrations.AddField(
            model_name="gameroom",
            name="paused_remaining_seconds",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
