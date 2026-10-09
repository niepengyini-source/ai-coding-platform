import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('coding', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectInvitation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code_digest', models.CharField(max_length=64, unique=True)),
                ('enabled', models.BooleanField(default=True)),
                ('expires_at', models.DateTimeField()),
                ('revision', models.PositiveIntegerField(default=1)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ('project', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='invitation', to='coding.project')),
            ],
        ),
        migrations.CreateModel(
            name='ProjectJoinRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', '等待审核'), ('approved', '已通过'), ('rejected', '未通过'), ('cancelled', '已撤回')], default='pending', max_length=12)),
                ('note', models.CharField(blank=True, max_length=500)),
                ('decision_note', models.CharField(blank=True, max_length=500)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('requested_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='join_requests', to='coding.project')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='project_join_requests', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reviewed_join_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-requested_at', '-id'],
                'constraints': [models.UniqueConstraint(fields=('project', 'user'), name='unique_project_join_request')],
            },
        ),
    ]
