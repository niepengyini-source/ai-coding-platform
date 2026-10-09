import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('coding', '0002_project_invitation_join_request'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.CreateModel(
        name='CodebookImportDraft',
        fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('filename', models.CharField(max_length=200)),
            ('raw', models.BinaryField()),
            ('preview', models.JSONField(default=dict)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('applied_book', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                              related_name='import_sources', to='coding.codebook')),
            ('book', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                      related_name='import_drafts', to='coding.codebook')),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
        ],
    )]
