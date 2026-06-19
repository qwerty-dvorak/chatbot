from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("knowledge", "0003_ragsearchlog")]

    operations = [
        migrations.AddField("document", "ocr_mode", models.CharField(default="none", max_length=30)),
        migrations.AddField("documentasset", "source_index", models.IntegerField(default=0)),
        migrations.AddField("documentasset", "derived_index", models.IntegerField(default=0)),
        migrations.AddField("documentasset", "object_key", models.CharField(default="", max_length=64)),
        migrations.AddField("documentasset", "sha256", models.CharField(default="", max_length=64)),
        migrations.AddField("documentasset", "width", models.IntegerField(default=0)),
        migrations.AddField("documentasset", "height", models.IntegerField(default=0)),
        migrations.AddField("documentasset", "ocr_backend", models.CharField(default="none", max_length=30)),
        migrations.AddField("documentasset", "ocr_status", models.CharField(default="skipped", max_length=30)),
        migrations.AddField("documentasset", "preprocessing", models.JSONField(blank=True, default=dict)),
        migrations.AddIndex(
            model_name="documentasset",
            index=models.Index(fields=["document", "source_index", "derived_index"], name="doc_asset_source_idx"),
        ),
        migrations.AddIndex(
            model_name="documentasset",
            index=models.Index(fields=["sha256"], name="doc_asset_sha_idx"),
        ),
    ]
