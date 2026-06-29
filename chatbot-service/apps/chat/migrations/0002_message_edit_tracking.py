# Generated for message edit auditing.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="edit_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="message",
            name="edited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="MessageEdit",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("previous_content", models.TextField(default="")),
                ("new_content", models.TextField(default="")),
                ("previous_metadata", models.JSONField(blank=True, default=dict)),
                ("new_metadata", models.JSONField(blank=True, default=dict)),
                ("superseded_message_ids", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "editor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="message_edits",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="edits",
                        to="chat.message",
                    ),
                ),
            ],
            options={
                "db_table": "message_edits",
            },
        ),
        migrations.AddIndex(
            model_name="messageedit",
            index=models.Index(fields=["message", "-created_at"], name="message_edi_message_e057a3_idx"),
        ),
        migrations.AddIndex(
            model_name="messageedit",
            index=models.Index(fields=["editor", "-created_at"], name="message_edi_editor__07a020_idx"),
        ),
    ]
