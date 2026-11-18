from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('coding', '0008_parallel_labels'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='SourceCodingTask', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('completed', models.BooleanField(default=False)),
            ('revision', models.PositiveIntegerField(default=0)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('round', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='source_tasks', to='coding.round')),
            ('document', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='coding_tasks', to='coding.sourcedocument')),
            ('coder', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
        ], options={'ordering': ['document_id', 'id']}),
        migrations.AddConstraint(model_name='sourcecodingtask', constraint=models.UniqueConstraint(fields=('round', 'document', 'coder'), name='unique_source_coding_task')),
        migrations.CreateModel(name='SourceAnnotation', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('start', models.PositiveIntegerField()), ('end', models.PositiveIntegerField()),
            ('text', models.TextField()), ('secondary', models.JSONField(default=list)),
            ('labels', models.JSONField(default=list)), ('uncertain', models.BooleanField(default=False)),
            ('no_code', models.BooleanField(default=False)), ('note', models.TextField(blank=True)),
            ('revision', models.PositiveIntegerField(default=0)), ('last_token', models.CharField(max_length=64, blank=True)),
            ('book_snapshot', models.JSONField(default=dict)),
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('primary', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='coding.code')),
            ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='annotations', to='coding.sourcecodingtask')),
        ], options={'ordering': ['start', 'end', 'id']}),
        migrations.AddConstraint(model_name='sourceannotation', constraint=models.UniqueConstraint(fields=('task', 'start', 'end'), name='unique_source_annotation_range')),
    ]
