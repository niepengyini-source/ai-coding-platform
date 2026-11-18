from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('coding', '0006_source_display_mapping')]
    operations = [
        migrations.AddField(model_name='sourcedocument', name='normalized',
                            field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='sourcedocument', name='workbench_mode',
                            field=models.CharField(choices=[('auto', '自动推荐'), ('unit', '逐条编码'),
                                                            ('document', '全文阅读')], default='auto', max_length=10)),
        migrations.AddField(model_name='sourcedocument', name='units_confirmed',
                            field=models.BooleanField(default=True)),
        migrations.AddField(model_name='sourcedocument', name='unit_revision',
                            field=models.PositiveIntegerField(default=0)),
    ]
