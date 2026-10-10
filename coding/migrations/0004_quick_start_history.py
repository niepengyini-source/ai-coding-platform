import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('coding', '0003_codebook_import_draft'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name='round', name='started_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='quick_coding_rounds', to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(
            model_name='round', name='kind',
            field=models.CharField(max_length=10, choices=[('training', '培训练习'), ('pilot', '独立试编码'),
                                                         ('formal', '正式编码'), ('quick', '个人快速编码')])),
        migrations.AddIndex(
            model_name='assignment',
            index=models.Index(fields=['coder', '-updated_at', '-id'], name='coding_history_by_user')),
    ]
