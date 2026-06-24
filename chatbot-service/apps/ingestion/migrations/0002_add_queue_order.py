# Generated manually — add queue_order field to IngestionJob

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ingestion", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingestionjob",
            name="queue_order",
            field=models.IntegerField(db_index=True, default=0),
        ),
    ]
